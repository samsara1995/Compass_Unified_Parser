# automatically generated by the FlatBuffers compiler, do not modify

# namespace: tflite

import flatbuffers


class GeluOptions(object):
    __slots__ = ['_tab']

    @classmethod
    def GetRootAsGeluOptions(cls, buf, offset):
        n = flatbuffers.encode.Get(flatbuffers.packer.uoffset, buf, offset)
        x = GeluOptions()
        x.Init(buf, n + offset)
        return x

    # GeluOptions
    def Init(self, buf, pos):
        self._tab = flatbuffers.table.Table(buf, pos)

    # GeluOptions
    def Approximate(self):
        o = flatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(4))
        if o != 0:
            return bool(self._tab.Get(flatbuffers.number_types.BoolFlags, o + self._tab.Pos))
        return False


def GeluOptionsStart(builder): builder.StartObject(1)
def GeluOptionsAddApproximate(builder, approximate): builder.PrependBoolSlot(0, approximate, 0)
def GeluOptionsEnd(builder): return builder.EndObject()
