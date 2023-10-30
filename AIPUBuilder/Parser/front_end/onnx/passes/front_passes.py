# SPDX-License-Identifier: Apache-2.0
# Copyright © 2022-2023 Arm Technology (China) Co. Ltd.

import copy
import numpy as np
from ....common.defs import Tensor
from ....ops.op import Op, OpHasWeights, OpHasBiases, KerasOp, BaseDeconvOp, ConstLikeOp, OpHasOneOutPort
from ....graph.node_wrap import NodeWrap
from ....graph.pattern_match import matched_patterns, single_node_matcher, two_nodes_matcher
from ....graph.graph_algo import get_valid_node_name, determined_sort
from ....logger import INFO, DEBUG, WARN, ERROR, FATAL
from .common_passes import clear_redundant_nodes, FLOAT_EQUAL, insert_constant


def fuse_weights_const(graph):
    def _get_src_data(src_name, edge_attr):
        src_obj = NodeWrap(graph, src_name)['object']
        if src_obj.type in ('Constant', 'TfConst'):
            data = src_obj.value
        elif (edge_attr.get('tensor', None) is not None and edge_attr['tensor'].is_const):
            data = edge_attr['tensor'].value
        else:
            data = None
        return data

    matched = False
    for node_name in graph.nodes:
        node_obj = NodeWrap(graph, node_name)['object']
        if node_obj is None:
            ERROR('[Parser]: Meets invalid Op(%s) in fuse_weights_const!' % node_name)
            continue
        if isinstance(node_obj, KerasOp):
            continue
        in_edges = graph.sorted_in_edges(node_name, keys=True, data=True)
        if isinstance(node_obj, OpHasWeights) and isinstance(node_obj, OpHasBiases):
            if node_obj.type in ('GRU', 'LSTM', 'QLinearConv', 'DeformConv'):
                continue
            if node_obj.type == 'LiteTRANSPOSE_CONV' \
                    or node_obj.type == 'LiteCONV_3D_TRANSPOSE':
                biases_in_port = 3
            else:
                biases_in_port = 2
            for i, edge_info in enumerate(in_edges):
                src_name, _, k, edge_attr = edge_info
                data = _get_src_data(src_name, edge_attr)
                try:
                    if i == 1 and isinstance(data, np.ndarray):
                        node_obj.weights = data
                        if edge_attr.get('tensor', None) is not None:
                            if len(edge_attr['tensor'].min_max) == 2:
                                node_obj.weights_range = list(
                                    edge_attr['tensor'].min_max)
                            if len(edge_attr['tensor'].scale_zp) == 2:
                                node_obj.weights_scale_zp = list(
                                    edge_attr['tensor'].scale_zp)
                        matched = True
                        graph.remove_edge(src_name, node_name, key=k)
                    elif i == biases_in_port and isinstance(data, np.ndarray):
                        node_obj.biases = data
                        if edge_attr.get('tensor', None) is not None:
                            if len(edge_attr['tensor'].min_max) == 2:
                                node_obj.biases_range = list(
                                    edge_attr['tensor'].min_max)
                            if len(edge_attr['tensor'].scale_zp) == 2:
                                node_obj.biases_scale_zp = list(
                                    edge_attr['tensor'].scale_zp)
                        matched = True
                        graph.remove_edge(src_name, node_name, key=k)
                except Exception as e:
                    ERROR('[Parser]: Node(%s) meets error (%s) in fuse_weights_const!' % (
                        node_name, str(e)))
        elif isinstance(node_obj, OpHasWeights):
            for i, edge_info in enumerate(in_edges):
                src_name, _, k, edge_attr = edge_info
                data = _get_src_data(src_name, edge_attr)
                if i == 1 and isinstance(data, np.ndarray):
                    node_obj.weights = data
                    if edge_attr.get('tensor', None) is not None:
                        if len(edge_attr['tensor'].min_max) == 2:
                            node_obj.weights_range = list(
                                edge_attr['tensor'].min_max)
                        if len(edge_attr['tensor'].scale_zp) == 2:
                            node_obj.weights_scale_zp = list(
                                edge_attr['tensor'].scale_zp)
                    matched = True
                    graph.remove_edge(src_name, node_name, key=k)
    if matched:
        clear_redundant_nodes(graph)


def convert_special_prelu(graph):
    matches = single_node_matcher(graph, 'PRelu')
    for m in matches:
        prelu = m['target']
        prelu_obj = NodeWrap(graph, prelu)['object']
        if prelu_obj is None:
            ERROR(
                '[Parser]: Meets invalid PRelu Op (%s) in convert_special_prelu!' % prelu)
            continue
        inputs = prelu_obj.get_input_tensors()
        in_edges = graph.sorted_in_edges(prelu, data=True)
        if len(inputs) != 2 or inputs[1] is None or len(in_edges) != 2:
            ERROR(
                '[Parser]: Meets invalid PRelu Op (%s) in convert_special_prelu!' % prelu)
            continue
        if in_edges[1][2]['tensor'] is not None \
                and in_edges[1][2]['tensor'].is_const \
                and inputs[1].size == 1:
            slope = np.reshape(inputs[1], [])
            graph.remove_edges_from(in_edges[1:])
            leaky_attr = prelu_obj.copied_attr()
            leaky_attr.update({'opeset_version': 6, 'alpha': float(slope)})
            NodeWrap(graph, prelu).replace_obj('LeakyRelu', leaky_attr)


def decompose_loop(graph, params):
    matched = False
    matches = single_node_matcher(graph, 'Loop')
    for m in matches:
        loop = m['target']
        loop_obj = NodeWrap(graph, loop)['object']
        in_edges = graph.sorted_in_edges(loop, data=True)
        loop_out_edges = graph.sorted_out_edges(loop, data=True)
        if loop_obj is not None \
                and len(in_edges) >= 2 + len(loop_obj.body._attr['root_in_ports']) \
                and len(loop_out_edges) >= 1:
            if not (len(in_edges) == (2 + len(loop_obj.body._attr['root_in_ports']))
                    or len(in_edges) == (3 + len(loop_obj.body._attr['root_in_ports'])))\
                    or not in_edges[0][2]['tensor'].is_const \
                    or not in_edges[1][2]['tensor'].is_const \
                    or in_edges[0][2]['tensor'].value is None \
                    or in_edges[1][2]['tensor'].value is None:
                continue

            condition = in_edges[1][2]['tensor'].value

            if len(loop_obj.body._attr['output_names']) == 3:
                subgraph_main_out = loop_obj.body._attr['output_names'][-2]
            else:
                ERROR('invalid loop, need to support more forms.')

            subgraph_main_outport = loop_obj.body._attr['output_ports'][subgraph_main_out]
            subgraph_main_nodes = determined_sort(
                loop_obj.body, [subgraph_main_out])

            # some constant nodes have been fused, skip checking them.
            subgraph_main_nodes = [
                x for x in subgraph_main_nodes if x in graph.nodes]

            subgraph_main_nodes_objs = {n: NodeWrap(
                graph, n)['object'] for n in subgraph_main_nodes}

            const_node_list = []
            for (node_obj_name, node_obj) in subgraph_main_nodes_objs.items():
                if node_obj is not None \
                        and not isinstance(node_obj, ConstLikeOp) \
                        and isinstance(node_obj, OpHasOneOutPort) \
                        and node_obj.is_all_inputs_const():
                    const_node_list.append(node_obj_name)

            if len(subgraph_main_nodes) > 0 \
                    and subgraph_main_out not in subgraph_main_nodes:
                WARN('[Parser]: Meets invalid Subgraph Nodes in decompose_const_loop!')
                continue

            try:
                if len(subgraph_main_nodes_objs[subgraph_main_out].get_output_tensors()) < 1:
                    continue
                main_out_tensor = subgraph_main_nodes_objs[subgraph_main_out].get_output_tensors()[
                    0]
            except:
                # TODO: subgraph_main_out node is None. Need to support more forms.
                pass

            matched = True
            count = int(in_edges[0][2]['tensor'].value)
            stack = get_valid_node_name(graph, loop + '_stack')

            for n in loop_obj.body._filter_node:
                try:
                    NodeWrap(graph, n)['object'].in_subgraph = False
                except:
                    pass

            graph.remove_edges_from(in_edges)
            # TODO: Condition False
            if not condition:
                for index, (_, dst, out_attr) in enumerate(loop_out_edges):
                    graph.remove_edge(loop, dst)
                    graph.add_edge(in_edges[-1][0], dst, **out_attr)
                continue

            last_loop_res = subgraph_main_out
            for i in range(count):
                if i == 0:
                    for n in subgraph_main_nodes:
                        n_obj = subgraph_main_nodes_objs[n]
                        n_in_edges = graph.sorted_in_edges(n, data=True)

                        for sub_src, _, in_attr in n_in_edges:
                            # reset iter_num in first subgraph
                            if sub_src == in_edges[0][0] and graph.nodes[sub_src]['op'] in ['Dummy', 'Constant']:
                                cur_count_value = np.array(
                                    i, np.dtype(np.int32))
                                in_attr['tensor'].value = cur_count_value
                                NodeWrap(graph, sub_src).replace_obj('Constant', {
                                    'name': sub_src, 'opset_version': 9, 'value': cur_count_value})

                        # TODO: some special nodes need to reset attr.
                        if n_obj.type == 'Slice':
                            cur_obj_attr = n_obj.copied_attr()
                            cur_obj_attr.update({'starts': None, 'ends': None})
                            NodeWrap(graph, n).replace_obj(
                                n_obj.type, cur_obj_attr)

                    graph.add_edge(subgraph_main_out,
                                   stack,
                                   **{'src_out_port': subgraph_main_outport,
                                      'dst_in_port': i,
                                      'tensor': Tensor(value=main_out_tensor)})

                else:
                    for n in subgraph_main_nodes:
                        name_suffix = '_loop_%s' % i
                        new_n = get_valid_node_name(graph, n + name_suffix)
                        n_obj = subgraph_main_nodes_objs[n]
                        n_in_edges = graph.sorted_in_edges(n, data=True)
                        if len(n_in_edges) == 0:
                            continue
                        for src, _, in_attr in n_in_edges:
                            if src not in subgraph_main_nodes and not src.endswith(name_suffix):
                                # nodes not in the sub graph.
                                if len(loop_obj.body._attr['output_names']) == 3 and not n in const_node_list:
                                    # add edge between last loop res with the first node of next loop.
                                    graph.add_edge(
                                        last_loop_res, new_n, **in_attr)
                                    last_loop_res = new_n
                                elif src == in_edges[0][0]:
                                    # change iter num for constant node.
                                    new_const = get_valid_node_name(
                                        graph, src + name_suffix)
                                    cur_count_value = np.array(
                                        i, np.dtype(np.int32))
                                    new_in_attr = copy.deepcopy(in_attr)
                                    new_in_attr['tensor'].value = cur_count_value
                                    new_in_attr['tensor'].name = new_const
                                    graph.add_edge(
                                        new_const, new_n, **new_in_attr)

                                    NodeWrap(graph, new_const).replace_obj('Constant', {
                                        'name': new_const, 'opset_version': 9, 'value': cur_count_value})
                                else:
                                    graph.add_edge(src, new_n, **in_attr)
                            elif src in subgraph_main_nodes:
                                # nodes in the sub graph
                                new_in_attr = copy.deepcopy(in_attr)

                                if n in subgraph_main_nodes:
                                    graph.add_edge(
                                        src + name_suffix, new_n, **new_in_attr)
                                    if graph.nodes[src + name_suffix]['op'] is None:
                                        src_obj = subgraph_main_nodes_objs[src]
                                        src_obj_attr = src_obj.copied_attr()
                                        src_obj_attr.update({'name': new_n})
                                        NodeWrap(
                                            graph, src + name_suffix).replace_obj(src_obj.type, src_obj_attr)
                                else:
                                    graph.add_edge(
                                        src + name_suffix, new_n, **new_in_attr)
                            else:
                                WARN(
                                    '[Parser]: Invalid in edges for Node(%s)!' % new_n)
                        cur_obj_attr = n_obj.copied_attr()
                        cur_obj_attr.update({'name': new_n})
                        if n_obj.type == 'Slice':
                            cur_obj_attr.update({'starts': None, 'ends': None})

                        NodeWrap(graph, new_n).replace_obj(
                            n_obj.type, cur_obj_attr)
                        if n == subgraph_main_out:
                            graph.add_edge(new_n,
                                           stack,
                                           **{'src_out_port': subgraph_main_outport,
                                              'dst_in_port': i,
                                              'tensor': Tensor(value=main_out_tensor)
                                              })
            if len(loop_out_edges) == 1:
                for _, dst, out_attr in loop_out_edges:
                    graph.remove_edge(loop, dst)
                    graph.add_edge(stack, dst, **out_attr)
            elif len(loop_out_edges) == 2:
                for index, (_, dst, out_attr) in enumerate(loop_out_edges):
                    graph.remove_edge(loop, dst)
                    if index == 1:
                        graph.add_edge(stack, dst, **out_attr)
            else:
                WARN('invalid loop out_edges, need to support.')
            NodeWrap(graph, stack).replace_obj('ConcatFromSequence', {
                'name': stack, 'opset_version': 11, 'axis': 0, 'new_axis': 1})

        else:
            ERROR(
                '[Parser]: Meets invalid Loop Op (%s) in decompose_const_loop!' % loop)

    if matched:
        if graph._attr.get('subgraph_output_names', None) is not None:
            graph._attr['output_names'] = list(set(graph._attr['output_names']).difference(
                list(graph._attr['subgraph_output_names'])))
            if loop in list(set(graph._attr['output_names'])):
                index = graph._attr['output_names'].index(loop)
                graph._attr['output_names'].pop(index)
                if condition:
                    graph._attr['output_names'].append(stack)
                    graph._attr['output_names'].append(last_loop_res)
                else:
                    graph._attr['output_names'].append(in_edges[-1][0])
        clear_redundant_nodes(graph)


def convert_special_sequence_construct(graph):
    '''Add Out node after inputs of sequence_construct and update graph outputs if
    the sequence_construct node is graph output.
    sequence_construct will be removed by clear_redundant_nodes if there is no path
    between it and other graph output, or be processed and removed by other passes
    otherwise.
    '''
    matched = False
    matches = single_node_matcher(graph, 'SequenceConstruct')
    for m in matches:
        seq_construct = m['target']
        seq_construct_obj = NodeWrap(graph, seq_construct)['object']
        if seq_construct_obj is None:
            ERROR(
                '[Parser]: Meets invalid SequenceConstruct Op (%s) in convert_special_sequence_construct!' % seq_construct)
            continue
        if seq_construct not in graph._attr['output_names']:
            continue
        matched = True
        WARN('[Parser]: SequenceConstruct Op (%s) will be converted to deconstructed tensors in graph outputs!' % seq_construct)
        index = graph._attr['output_names'].index(seq_construct)
        graph._attr['output_names'].pop(index)
        in_edges = graph.sorted_in_edges(seq_construct, data=True)
        for idx, (name, _, in_attr) in enumerate(in_edges):
            out_name = get_valid_node_name(graph, name + '_out')
            out_in_attr = copy.deepcopy(in_attr)
            out_in_attr.update({'dst_in_port': 0})
            graph.add_edge(name, out_name, **out_in_attr)
            NodeWrap(graph, out_name).replace_obj('Out', {'name': out_name})
            graph._attr['output_names'].insert(index+idx, name)
    if matched:
        clear_redundant_nodes(graph)


def convert_deconv(graph):
    deconv_ops = BaseDeconvOp.get_concrete_subclass_names()
    framework_ops = Op.framework_op_types(graph._attr['framework'])
    current_deconvs = list(set(deconv_ops).intersection(framework_ops))
    matches = single_node_matcher(graph, current_deconvs)
    for m in matches:
        deconv = m['target']
        deconv_obj = NodeWrap(graph, deconv)['object']
        if deconv_obj is None:
            ERROR('[Parser]: Meets invalid Deconv Op(%s) in convert_deconv!' % deconv)
            continue
        main_in_port = type(deconv_obj).main_in_port()
        input_shapes = deconv_obj.get_input_shapes()
        in_edges = graph.sorted_in_edges(deconv, data=True)
        if len(input_shapes) >= 0 \
                and len(input_shapes) > main_in_port \
                and input_shapes[main_in_port] is not None \
                and all(s is not None for s in input_shapes[main_in_port]) \
                and len(input_shapes) == len(in_edges):
            src, _, in_attr = in_edges[main_in_port]
            graph.remove_edges_from(in_edges)
            in_attr['dst_in_port'] = 0
            graph.add_edge(src, deconv, **in_attr)
            in_shape = input_shapes[main_in_port]
            spatial_in_shape = in_shape[1:-1] if deconv_obj.data_format == 'NHWC' else in_shape[2:]
            deconv_obj.update_pads(spatial_in_shape)
            new_weights = np.transpose(deconv_obj.weights, axes=type(deconv_obj).perm_lite_to_onnx())
            attrs = deconv_obj.copied_attr()
            attrs.update({'opset_version': 11, 'weights': new_weights})
            NodeWrap(graph, deconv).replace_obj('ConvTranspose', attrs)


def merge_qconv(graph):
    if not graph._attr.get('quantize', False):
        return
    matched = False
    matches = matched_patterns(graph,
                               nodes=[
                                   ('x_dequant', {'op': 'DequantizeLinear'}),
                                   ('w_dequant', {'op': 'DequantizeLinear'}),
                                   ('b_dequant', {'op': 'DequantizeLinear'}),
                                   ('conv', {'op': ['Conv', 'ConvTranspose']}),
                                   ('y_quant', {'op': 'QuantizeLinear'}),
                               ],
                               edges=[
                                   ('x_dequant', 'conv'),
                                   ('w_dequant', 'conv', {'dst_in_port': 1}),
                                   ('b_dequant', 'conv', {'dst_in_port': 2}),
                                   ('conv', 'y_quant')
                               ])
    matches_with_relu = matched_patterns(graph,
                                         nodes=[
                                             ('x_dequant', {
                                              'op': 'DequantizeLinear'}),
                                             ('w_dequant', {
                                              'op': 'DequantizeLinear'}),
                                             ('b_dequant', {
                                              'op': 'DequantizeLinear'}),
                                             ('conv', {'op': 'Conv'}),
                                             ('relu', {'op': 'Relu'}),
                                             ('y_quant', {
                                              'op': 'QuantizeLinear'}),
                                         ],
                                         edges=[
                                             ('x_dequant', 'conv'),
                                             ('w_dequant', 'conv',
                                              {'dst_in_port': 1}),
                                             ('b_dequant', 'conv',
                                              {'dst_in_port': 2}),
                                             ('conv', 'relu'),
                                             ('relu', 'y_quant'),
                                         ])
    for m in matches + matches_with_relu:
        names = ['x_dequant', 'w_dequant', 'b_dequant', 'conv',
                 'y_quant'] + (['relu'] if 'relu' in m else [])
        obj_dict = {n: NodeWrap(graph, m[n])['object'] for n in names}
        if any(v is None for v in obj_dict.values()):
            error_node = [n for n in obj_dict if obj_dict[n] is None][0]
            ERROR('[Parser]: Meets invalid Op(%s) in merge_qconv!' % error_node)
            continue
        x_dequant_in_edges = graph.sorted_in_edges(m['x_dequant'], data=True)
        if len(x_dequant_in_edges) not in (2, 3):
            ERROR(
                '[Parser]: Meets invalid Dequantize Op(%s) in merge_qconv!' % m['x_dequant'])
            continue
        if any(e[2]['tensor'].value is None for e in x_dequant_in_edges[1:]) \
                or any(not e[2]['tensor'].is_const for e in x_dequant_in_edges[1:]):
            continue
        w_dequant_in_edges = graph.sorted_in_edges(m['w_dequant'], data=True)
        if len(w_dequant_in_edges) not in (2, 3):
            ERROR(
                '[Parser]: Meets invalid Dequantize Op(%s) in merge_qconv!' % m['w_dequant'])
            continue
        if any(e[2]['tensor'].value is None for e in w_dequant_in_edges) \
                or any(not e[2]['tensor'].is_const for e in w_dequant_in_edges):
            continue
        b_dequant_in_edges = graph.sorted_in_edges(m['b_dequant'], data=True)
        if len(b_dequant_in_edges) not in (2, 3):
            ERROR(
                '[Parser]: Meets invalid Dequantize Op(%s) in merge_qconv!' % m['b_dequant'])
            continue
        if any(e[2]['tensor'].value is None for e in b_dequant_in_edges) \
                or any(not e[2]['tensor'].is_const for e in b_dequant_in_edges):
            continue
        conv_out_edges = graph.sorted_out_edges(m['conv'], data=True)
        if len(conv_out_edges) != 1:
            continue
        relu = m['relu'] if 'relu' in m else None
        if relu is not None and len(graph.sorted_out_edges(relu)) != 1:
            continue
        y_quant_in_edges = graph.sorted_in_edges(m['y_quant'], data=True)
        if len(y_quant_in_edges) not in (2, 3):
            ERROR('[Parser]: Meets invalid Quantize Op(%s) in merge_qconv!' %
                  m['y_quant'])
            continue
        if any(e[2]['tensor'].value is None for e in y_quant_in_edges[1:]) \
                or any(not e[2]['tensor'].is_const for e in y_quant_in_edges[1:]):
            continue

        src, _, in_attr = x_dequant_in_edges[0]
        x_scale, x_zp = obj_dict['x_dequant'].x_scale, obj_dict['x_dequant'].x_zero_point
        w_scale, w_zp = obj_dict['w_dequant'].x_scale, obj_dict['w_dequant'].x_zero_point
        b_scale, b_zp = obj_dict['b_dequant'].x_scale, obj_dict['b_dequant'].x_zero_point
        y_scale, y_zp = obj_dict['y_quant'].y_scale, obj_dict['y_quant'].y_zero_point
        weights = w_dequant_in_edges[0][2]['tensor'].value
        biases = b_dequant_in_edges[0][2]['tensor'].value

        if not FLOAT_EQUAL(w_scale*x_scale, b_scale) or not np.all(b_zp == 0):
            continue

        matched = True
        new_in_attr = copy.deepcopy(in_attr)
        new_in_attr['tensor'].dtype = str(x_zp.dtype)
        new_in_attr['tensor'].scale_zp = (x_scale, x_zp)
        graph.remove_edges_from(
            graph.sorted_in_edges(m['conv']) + conv_out_edges)
        graph.add_edge(src, m['conv'], **new_in_attr)
        last_node = m['conv']
        if relu is not None:
            graph.remove_edges_from(graph.sorted_out_edges(relu))
            conv_out_attr = conv_out_edges[0][2]
            conv_out_attr['tensor'].dtype = str(y_zp.dtype)
            conv_out_attr['tensor'].scale_zp = (y_scale, y_zp)
            graph.add_edge(m['conv'], relu, **conv_out_attr)
            last_node = relu
        for _, dst, out_attr in graph.sorted_out_edges(m['y_quant'], data=True):
            graph.remove_edge(m['y_quant'], dst)
            out_attr['tensor'].dtype = str(y_zp.dtype)
            out_attr['tensor'].scale_zp = (y_scale, y_zp)
            graph.add_edge(last_node, dst, **out_attr)

        if m['y_quant'] in graph._attr['output_names']:
            index = graph._attr['output_names'].index(m['y_quant'])
            graph._attr['output_names'][index] = last_node

        conv_attr = obj_dict['conv'].copied_attr()
        if obj_dict['conv'].type == 'Conv':
            op_type = 'QLinearConv'
            conv_attr.update({'opset_version': 10})
            insert_constant(graph, m['conv'] + '_x_scale',
                            x_scale, m['conv'], in_port=1, data_format='NHWC')
            insert_constant(graph, m['conv'] + '_x_zero_point',
                            x_zp, m['conv'], in_port=2, data_format='NHWC')
            insert_constant(graph, m['conv'] + '_w', weights,
                            m['conv'], in_port=3, data_format='NHWC')
            insert_constant(graph, m['conv'] + '_w_scale',
                            w_scale, m['conv'], in_port=4, data_format='NHWC')
            insert_constant(graph, m['conv'] + '_w_zero_point',
                            w_zp, m['conv'], in_port=5, data_format='NHWC')
            insert_constant(graph, m['conv'] + '_y_scale',
                            y_scale, m['conv'], in_port=6, data_format='NHWC')
            insert_constant(graph, m['conv'] + '_y_zero_point',
                            y_zp, m['conv'], in_port=7, data_format='NHWC')
            insert_constant(graph, m['conv'] + '_B', biases,
                            m['conv'], in_port=8, data_format='NHWC')
        else:
            op_type = 'ConvTranspose'
            conv_attr.update({'opset_version': 11,
                              'weights': weights, 'weights_scale_zp': [w_scale, w_zp],
                              'biases': biases, 'biases_scale_zp': [b_scale, b_zp]})

        NodeWrap(graph, m['conv']).replace_obj(op_type, conv_attr)

    if matched:
        clear_redundant_nodes(graph)


def merge_qmatmul(graph):
    if not graph._attr.get('quantize', False):
        return
    matched = False
    matches = matched_patterns(graph,
                               nodes=[
                                   ('a_dequant', {'op': 'DequantizeLinear'}),
                                   ('b_dequant', {'op': 'DequantizeLinear'}),
                                   ('matmul', {'op': 'MatMul'}),
                                   ('y_quant', {'op': 'QuantizeLinear'}),
                               ],
                               edges=[
                                   ('a_dequant', 'matmul'),
                                   ('b_dequant', 'matmul', {'dst_in_port': 1}),
                                   ('matmul', 'y_quant')
                               ])
    for m in matches:
        names = ['a_dequant', 'b_dequant', 'matmul', 'y_quant']
        obj_dict = {n: NodeWrap(graph, m[n])['object'] for n in names}
        if any(v is None for v in obj_dict.values()):
            error_node = [n for n in obj_dict if obj_dict[n] is None][0]
            ERROR('[Parser]: Meets invalid Op(%s) in merge_qmatmul!' % error_node)
            continue
        a_dequant_in_edges = graph.sorted_in_edges(m['a_dequant'], data=True)
        if len(a_dequant_in_edges) not in (2, 3):
            ERROR('[Parser]: Meets invalid Dequantize Op(%s) in merge_qmatmul!' % m['x_dequant'])
            continue
        if any(e[2]['tensor'].value is None for e in a_dequant_in_edges[1:]):
            continue
        b_dequant_in_edges = graph.sorted_in_edges(m['b_dequant'], data=True)
        if len(b_dequant_in_edges) not in (2, 3):
            ERROR('[Parser]: Meets invalid Dequantize Op(%s) in merge_qmatmul!' % m['b_dequant'])
            continue
        if any(e[2]['tensor'].value is None for e in b_dequant_in_edges[1:]):
            continue
        matmul_out_edges = graph.sorted_out_edges(m['matmul'], data=True)
        if len(matmul_out_edges) != 1:
            continue
        y_quant_in_edges = graph.sorted_in_edges(m['y_quant'], data=True)
        if len(y_quant_in_edges) not in (2, 3):
            ERROR('[Parser]: Meets invalid Quantize Op(%s) in merge_qmatmul!' % m['y_quant'])
            continue
        if any(e[2]['tensor'].value is None for e in y_quant_in_edges[1:]):
            continue

        matched = True
        a_zp = obj_dict['a_dequant'].x_zero_point
        b_zp = obj_dict['b_dequant'].x_zero_point
        y_zp = obj_dict['y_quant'].y_zero_point

        matmul_in_edges = graph.sorted_in_edges(m['matmul'])
        graph.remove_edges_from(matmul_in_edges)
        for src, _, in_attr in a_dequant_in_edges:
            new_in_attr = copy.deepcopy(in_attr)
            graph.add_edge(src, m['matmul'], **new_in_attr)
        if len(a_dequant_in_edges) == 2:
            insert_constant(graph, m['matmul'] + '_a_zero_point', a_zp, m['matmul'], in_port=2, data_format='NHWC')
        for src, _, in_attr in b_dequant_in_edges:
            new_in_attr = copy.deepcopy(in_attr)
            new_in_attr['dst_in_port'] += 3
            graph.add_edge(src, m['matmul'], **new_in_attr)
        if len(b_dequant_in_edges) == 2:
            insert_constant(graph, m['matmul'] + '_b_zero_point', b_zp, m['matmul'], in_port=5, data_format='NHWC')
        for src, _, in_attr in y_quant_in_edges[1:]:
            new_in_attr = copy.deepcopy(in_attr)
            new_in_attr['dst_in_port'] += 5
            graph.add_edge(src, m['matmul'], **new_in_attr)
        if len(y_quant_in_edges) == 2:
            insert_constant(graph, m['matmul'] + '_y_zero_point', y_zp, m['matmul'], in_port=7, data_format='NHWC')

        graph.remove_edge(m['matmul'], m['y_quant'])
        y_quant_out_edges = graph.sorted_out_edges(m['y_quant'], data=True)
        for _, dst, out_attr in y_quant_out_edges:
            graph.remove_edge(m['y_quant'], dst)
            graph.add_edge(m['matmul'], dst, **out_attr)

        if m['y_quant'] in graph._attr['output_names']:
            index = graph._attr['output_names'].index(m['y_quant'])
            graph._attr['output_names'][index] = m['matmul']

        matmul_attr = obj_dict['matmul'].copied_attr()
        matmul_attr.update({'opset_version': 10, 'quantize': True})
        NodeWrap(graph, m['matmul']).replace_obj('QLinearMatMul', matmul_attr)

    if matched:
        clear_redundant_nodes(graph)


def merge_q_multiple(graph, op_list):
    if not graph._attr.get('quantize', False):
        return
    if not op_list:
        return
    if not isinstance(op_list, (list, tuple)):
        op_list = [op_list]
    else:
        op_list = list(op_list)

    matched = False
    matches = matched_patterns(graph,
                               nodes=[
                                   ('float_op', {'op': op_list}),
                                   ('quant', {'op': 'QuantizeLinear'}),
                               ],
                               edges=[
                                   ('float_op', 'quant')
                               ])
    for m in matches:
        in_edges = graph.sorted_in_edges(m['float_op'], data=True)
        if len(in_edges) < 1:
            ERROR('[Parser]: Meets invalid Concat Op(%s) in merge_q_multiple!' % m['float_op'])
            continue
        out_edges = graph.sorted_out_edges(m['float_op'], data=True)
        if len(out_edges) != 1:
            continue

        op_in_names = [e[0] for e in in_edges]
        names = op_in_names + [m['float_op'], m['quant']]
        obj_dict = {n: NodeWrap(graph, n)['object'] for n in names}
        if any(v is None for v in obj_dict.values()):
            error_node = [n for n in obj_dict if obj_dict[n] is None][0]
            ERROR('[Parser]: Meets invalid Op(%s) in merge_q_multiple!' % error_node)
            continue
        if any(obj_dict[n].type != 'DequantizeLinear' for n in op_in_names):
            continue

        found_invalid_dequant = False
        for dequant in op_in_names:
            dequant_in_edges = graph.sorted_in_edges(dequant, data=True)
            if len(dequant_in_edges) not in (2, 3):
                ERROR('[Parser]: Meets invalid Quantize Op(%s) in merge_q_multiple!' % dequant)
                found_invalid_dequant = True
                continue
            if any(e[2]['tensor'].value is None for e in dequant_in_edges[1:]) \
                    or any(not e[2]['tensor'].is_const for e in dequant_in_edges[1:]):
                found_invalid_dequant = True
                continue
        if found_invalid_dequant:
            continue

        quant_in_edges = graph.sorted_in_edges(m['quant'], data=True)
        if len(quant_in_edges) not in (2, 3):
            ERROR('[Parser]: Meets invalid Quantize Op(%s) in merge_q_multiple!' % m['quant'])
            continue
        if any(e[2]['tensor'].value is None for e in quant_in_edges[1:]) \
                or any(not e[2]['tensor'].is_const for e in quant_in_edges[1:]):
            continue

        matched = True

        y_scale, y_zp = obj_dict[m['quant']].y_scale, obj_dict[m['quant']].y_zero_point

        graph.remove_edges_from(in_edges)
        graph.remove_edge(m['float_op'], m['quant'])

        for i, dequant in enumerate(op_in_names):
            dequant_in_edges = graph.sorted_in_edges(dequant, data=True)
            src, _, in_attr = dequant_in_edges[0]
            new_in_attr = copy.deepcopy(in_attr)
            new_in_attr['dst_in_port'] = i
            x_scale, x_zp = obj_dict[dequant].x_scale, obj_dict[dequant].x_zero_point
            new_in_attr['tensor'].dtype = str(x_zp.dtype)
            new_in_attr['tensor'].scale_zp = (x_scale, x_zp)
            graph.add_edge(src, m['float_op'], **new_in_attr)

        for _, dst, out_attr in graph.sorted_out_edges(m['quant'], data=True):
            graph.remove_edge(m['quant'], dst)
            out_attr['tensor'].dtype = str(y_zp.dtype)
            out_attr['tensor'].scale_zp = (y_scale, y_zp)
            graph.add_edge(m['float_op'], dst, **out_attr)

        if m['quant'] in graph._attr['output_names']:
            index = graph._attr['output_names'].index(m['quant'])
            graph._attr['output_names'][index] = m['float_op']

        obj_dict[m['float_op']].quantize = True

    if matched:
        clear_redundant_nodes(graph)


def merge_q_unary(graph, op_list):
    if not graph._attr.get('quantize', False):
        return

    if not op_list:
        return
    if not isinstance(op_list, (list, tuple)):
        op_list = [op_list]
    else:
        op_list = list(op_list)

    matched = False
    matches = matched_patterns(graph,
                               nodes=[
                                   ('dequant', {
                                    'op': 'DequantizeLinear', 'unique': False}),
                                   ('float_op', {'op': op_list}),
                                   ('quant', {'op': 'QuantizeLinear'}),
                               ],
                               edges=[
                                   ('dequant', 'float_op', {'dst_in_port': 0}),
                                   ('float_op', 'quant')
                               ])
    for m in matches:
        names = ['dequant', 'float_op', 'quant']
        obj_dict = {n: NodeWrap(graph, m[n])['object'] for n in names}
        if any(v is None for v in obj_dict.values()):
            error_node = [n for n in obj_dict if obj_dict[n] is None][0]
            ERROR('[Parser]: Meets invalid Op(%s) in merge_q_unary!' %
                  error_node)
            continue
        dequant_in_edges = graph.sorted_in_edges(m['dequant'], data=True)
        if len(dequant_in_edges) not in (2, 3):
            ERROR(
                '[Parser]: Meets invalid Dequantize Op(%s) in merge_q_unary!' % m['dequant'])
            continue
        if any(e[2]['tensor'].value is None for e in dequant_in_edges[1:]) \
                or any(not e[2]['tensor'].is_const for e in dequant_in_edges[1:]):
            continue

        op_in_edges = graph.sorted_in_edges(m['float_op'], data=True)
        if len(op_in_edges) < 1:
            ERROR('[Parser]: Meets invalid Op(%s) in merge_q_unary!' %
                  m['float_op'])
            continue
        op_out_edges = graph.sorted_out_edges(m['float_op'], data=True)
        if len(op_out_edges) != 1:
            continue

        quant_in_edges = graph.sorted_in_edges(m['quant'], data=True)
        if len(quant_in_edges) not in (2, 3):
            ERROR(
                '[Parser]: Meets invalid Quantize Op(%s) in merge_q_unary!' % m['quant'])
            continue
        if any(e[2]['tensor'].value is None for e in quant_in_edges[1:]) \
                or any(not e[2]['tensor'].is_const for e in quant_in_edges[1:]):
            continue
        if obj_dict['float_op'].type == 'Clip':
            if len(op_in_edges) != 3\
                    or op_in_edges[1][2]['tensor'] is None \
                    or not op_in_edges[1][2]['tensor'].is_const\
                    or op_in_edges[2][2]['tensor'] is None \
                    or not op_in_edges[2][2]['tensor'].is_const:
                WARN(
                    '[Parser]: Meets invaild clip value for Op (%s) in merge_q_unary!' % m['float_op'])
                continue

        matched = True

        x_scale, x_zp = obj_dict['dequant'].x_scale, obj_dict['dequant'].x_zero_point
        y_scale, y_zp = obj_dict['quant'].y_scale, obj_dict['quant'].y_zero_point

        if obj_dict['float_op'].type == 'Clip':
            graph.remove_edges_from(op_in_edges[1:])
            clip_min = op_in_edges[1][2]['tensor'].value
            clip_max = op_in_edges[2][2]['tensor'].value

            q_min = np.iinfo(x_zp.dtype).min
            q_max = np.iinfo(x_zp.dtype).max

            q_clip_min = np.array(
                np.clip(clip_min/x_scale+x_zp, q_min, q_max)).astype(x_zp.dtype)
            q_clip_max = np.array(
                np.clip(clip_max/x_scale+x_zp, q_min, q_max)).astype(x_zp.dtype)

            insert_constant(graph, m['float_op'] + '_q_clip_min',
                            q_clip_min, m['float_op'], in_port=1)
            insert_constant(graph, m['float_op'] + '_q_clip_max',
                            q_clip_max, m['float_op'], in_port=2)
        elif obj_dict['float_op'].type in ('Sigmoid', 'LeakyRelu', 'HardSwish', 'HardSigmoid', 'Relu') \
                and y_zp.dtype == 'int32':
            y_zp = y_zp.astype(np.int16)
            WARN(
                '[Parser]: Op (%s) output zeropoint dtype is int32, now convert it to int16!' % m['float_op'])

        src, _, in_attr = dequant_in_edges[0]
        new_in_attr = copy.deepcopy(in_attr)
        new_in_attr['tensor'].dtype = str(x_zp.dtype)
        new_in_attr['tensor'].scale_zp = (x_scale, x_zp)

        graph.remove_edges_from(op_in_edges[:1])
        graph.remove_edge(m['float_op'], m['quant'])
        graph.add_edge(src, m['float_op'], **new_in_attr)
        for _, dst, out_attr in graph.sorted_out_edges(m['quant'], data=True):
            graph.remove_edge(m['quant'], dst)
            out_attr['tensor'].dtype = str(y_zp.dtype)
            out_attr['tensor'].scale_zp = (y_scale, y_zp)
            graph.add_edge(m['float_op'], dst, **out_attr)
        if m['quant'] in graph._attr['output_names']:
            index = graph._attr['output_names'].index(m['quant'])
            graph._attr['output_names'][index] = m['float_op']
        obj_dict['float_op'].quantize = True

    if matched:
        clear_redundant_nodes(graph)


def merge_sequence_construct_and_at(graph):
    matched = False
    matches = two_nodes_matcher(graph, 'SequenceConstruct', 'SequenceAt')
    for m in matches:
        seq_construct, seq_at = m['begin'], m['end']
        seq_construct_obj = NodeWrap(graph, seq_construct)['object']
        seq_at_obj = NodeWrap(graph, seq_at)['object']
        construct_in_edges = graph.sorted_in_edges(seq_construct, data=True)
        seq_num = len(construct_in_edges)
        if seq_construct_obj is None or seq_at_obj is None or seq_num < 1:
            ERROR(
                '[Parser]: Meets invalid SequenceConstruct/SequenceAt Op in merge_sequence_construct_and_at!')
            continue
        at_in_edges = graph.sorted_in_edges(seq_at, data=True)
        if len(at_in_edges) != 2 or at_in_edges[1][2]['tensor'] is None \
                or not at_in_edges[1][2]['tensor'].is_const:
            WARN('[Parser]: Only supports SequenceAt Op (%s) with constant position in merge_sequence_construct_and_at!' % seq_construct)
            continue
        position = at_in_edges[1][2]['tensor'].value
        if position < 0:
            position = position + seq_num
        if position < 0 or position >= seq_num:
            ERROR(
                '[Parser]: Meets invalid position(%d) of SequenceAt Op (%s) in merge_sequence_construct_and_at!' % (position, seq_at))
            continue
        matched = True
        at_out_edges = graph.sorted_out_edges(seq_at, data=True)
        graph.remove_edges_from(at_out_edges)
        src, _, in_attr = construct_in_edges[position]
        for _, dst, out_attr in at_out_edges:
            dst_in_attr = copy.deepcopy(in_attr)
            dst_in_attr.update({'dst_in_port': out_attr['dst_in_port']})
            graph.add_edge(src, dst, **dst_in_attr)
        if seq_at in graph._attr['output_names']:
            index = graph._attr['output_names'].index(seq_at)
            graph._attr['output_names'][index] = src
    if matched:
        clear_redundant_nodes(graph)
