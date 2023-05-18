# automatically generated by the FlatBuffers compiler, do not modify

# namespace: tflite

import flatbuffers


class SoftmaxOptions(object):
    __slots__ = ['_tab']

    @classmethod
    def GetRootAsSoftmaxOptions(cls, buf, offset):
        n = flatbuffers.encode.Get(flatbuffers.packer.uoffset, buf, offset)
        x = SoftmaxOptions()
        x.Init(buf, n + offset)
        return x

    # SoftmaxOptions
    def Init(self, buf, pos):
        self._tab = flatbuffers.table.Table(buf, pos)

    # SoftmaxOptions
    def Beta(self):
        o = flatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(4))
        if o != 0:
            return self._tab.Get(flatbuffers.number_types.Float32Flags, o + self._tab.Pos)
        return 0.0


def SoftmaxOptionsStart(builder): builder.StartObject(1)
def SoftmaxOptionsAddBeta(builder, beta): builder.PrependFloat32Slot(0, beta, 0.0)
def SoftmaxOptionsEnd(builder): return builder.EndObject()
