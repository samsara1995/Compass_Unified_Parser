# automatically generated by the FlatBuffers compiler, do not modify

# namespace: tflite

import flatbuffers


class DensifyOptions(object):
    __slots__ = ['_tab']

    @classmethod
    def GetRootAsDensifyOptions(cls, buf, offset):
        n = flatbuffers.encode.Get(flatbuffers.packer.uoffset, buf, offset)
        x = DensifyOptions()
        x.Init(buf, n + offset)
        return x

    # DensifyOptions
    def Init(self, buf, pos):
        self._tab = flatbuffers.table.Table(buf, pos)


def DensifyOptionsStart(builder): builder.StartObject(0)
def DensifyOptionsEnd(builder): return builder.EndObject()
