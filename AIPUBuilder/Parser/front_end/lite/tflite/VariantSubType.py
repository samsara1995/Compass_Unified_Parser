# automatically generated by the FlatBuffers compiler, do not modify

# namespace: tflite

import flatbuffers


class VariantSubType(object):
    __slots__ = ['_tab']

    @classmethod
    def GetRootAsVariantSubType(cls, buf, offset):
        n = flatbuffers.encode.Get(flatbuffers.packer.uoffset, buf, offset)
        x = VariantSubType()
        x.Init(buf, n + offset)
        return x

    # VariantSubType
    def Init(self, buf, pos):
        self._tab = flatbuffers.table.Table(buf, pos)

    # VariantSubType
    def Shape(self, j):
        o = flatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(4))
        if o != 0:
            a = self._tab.Vector(o)
            return self._tab.Get(flatbuffers.number_types.Int32Flags, a + flatbuffers.number_types.UOffsetTFlags.py_type(j * 4))
        return 0

    # VariantSubType
    def ShapeAsNumpy(self):
        o = flatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(4))
        if o != 0:
            return self._tab.GetVectorAsNumpy(flatbuffers.number_types.Int32Flags, o)
        return 0

    # VariantSubType
    def ShapeLength(self):
        o = flatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(4))
        if o != 0:
            return self._tab.VectorLen(o)
        return 0

    # VariantSubType
    def Type(self):
        o = flatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(6))
        if o != 0:
            return self._tab.Get(flatbuffers.number_types.Int8Flags, o + self._tab.Pos)
        return 0

    # VariantSubType
    def HasRank(self):
        o = flatbuffers.number_types.UOffsetTFlags.py_type(self._tab.Offset(8))
        if o != 0:
            return bool(self._tab.Get(flatbuffers.number_types.BoolFlags, o + self._tab.Pos))
        return False


def VariantSubTypeStart(builder): builder.StartObject(3)
def VariantSubTypeAddShape(builder, shape): builder.PrependUOffsetTRelativeSlot(
    0, flatbuffers.number_types.UOffsetTFlags.py_type(shape), 0)


def VariantSubTypeStartShapeVector(builder, numElems): return builder.StartVector(4, numElems, 4)
def VariantSubTypeAddType(builder, type): builder.PrependInt8Slot(1, type, 0)
def VariantSubTypeAddHasRank(builder, hasRank): builder.PrependBoolSlot(2, hasRank, 0)
def VariantSubTypeEnd(builder): return builder.EndObject()
