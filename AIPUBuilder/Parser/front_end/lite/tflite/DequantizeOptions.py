# automatically generated by the FlatBuffers compiler, do not modify

# namespace: tflite

import flatbuffers


class DequantizeOptions(object):
    __slots__ = ['_tab']

    @classmethod
    def GetRootAsDequantizeOptions(cls, buf, offset):
        n = flatbuffers.encode.Get(flatbuffers.packer.uoffset, buf, offset)
        x = DequantizeOptions()
        x.Init(buf, n + offset)
        return x

    # DequantizeOptions
    def Init(self, buf, pos):
        self._tab = flatbuffers.table.Table(buf, pos)


def DequantizeOptionsStart(builder): builder.StartObject(0)
def DequantizeOptionsEnd(builder): return builder.EndObject()
