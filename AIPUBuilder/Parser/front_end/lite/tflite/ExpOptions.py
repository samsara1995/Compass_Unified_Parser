# automatically generated by the FlatBuffers compiler, do not modify

# namespace: tflite

import flatbuffers


class ExpOptions(object):
    __slots__ = ['_tab']

    @classmethod
    def GetRootAsExpOptions(cls, buf, offset):
        n = flatbuffers.encode.Get(flatbuffers.packer.uoffset, buf, offset)
        x = ExpOptions()
        x.Init(buf, n + offset)
        return x

    # ExpOptions
    def Init(self, buf, pos):
        self._tab = flatbuffers.table.Table(buf, pos)


def ExpOptionsStart(builder): builder.StartObject(0)
def ExpOptionsEnd(builder): return builder.EndObject()
