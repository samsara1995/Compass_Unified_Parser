# automatically generated by the FlatBuffers compiler, do not modify

# namespace: tflite

import flatbuffers


class NonMaxSuppressionV5Options(object):
    __slots__ = ['_tab']

    @classmethod
    def GetRootAsNonMaxSuppressionV5Options(cls, buf, offset):
        n = flatbuffers.encode.Get(flatbuffers.packer.uoffset, buf, offset)
        x = NonMaxSuppressionV5Options()
        x.Init(buf, n + offset)
        return x

    # NonMaxSuppressionV5Options
    def Init(self, buf, pos):
        self._tab = flatbuffers.table.Table(buf, pos)


def NonMaxSuppressionV5OptionsStart(builder): builder.StartObject(0)
def NonMaxSuppressionV5OptionsEnd(builder): return builder.EndObject()
