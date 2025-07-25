# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import array
import datetime
import enum
import logging
import math
import os
import typing

import pyfory
import pytest
from dataclasses import dataclass
from pyfory.util import lazy_import
from typing import List, Dict, Any


pa = lazy_import("pyarrow")


def debug_print(*params):
    """print params if debug is needed."""
    # print(*params)


def cross_language_test(test_func):
    env_key = "ENABLE_CROSS_LANGUAGE_TESTS"
    test_func = pytest.mark.skipif(
        env_key not in os.environ,
        reason=f"Pass {env_key} to enable cross-language tests",
    )(test_func)
    return test_func


Foo = pyfory.record_class_factory("Foo", ["f" + str(i) for i in range(1, 6)])
Bar = pyfory.record_class_factory("Bar", ["f" + str(i) for i in range(1, 3)])


def create_bar_schema():
    bar_schema = pa.schema(
        [
            ("f1", pa.int32()),
            ("f2", pa.utf8()),
        ]
    )
    return bar_schema


def create_foo_schema():
    foo_schema = pa.schema(
        [
            ("f1", pa.int32()),
            ("f2", pa.utf8()),
            ("f3", pa.list_(pa.utf8())),
            ("f4", pa.map_(pa.utf8(), pa.int32())),
            pa.field(
                "f5",
                pa.struct(create_bar_schema()),
                metadata={"cls": pyfory.get_qualified_classname(Bar)},
            ),
        ],
        metadata={"cls": pyfory.get_qualified_classname(Foo)},
    )
    return foo_schema


@dataclass
class FooPOJO:
    f1: "pa.int32"
    f2: str
    f3: List[str]
    f4: Dict[str, "pa.int32"]
    f5: "BarPOJO"


@dataclass
class BarPOJO:
    f1: "pa.int32"
    f2: str


def create_foo(foo_cls=Foo, bar_cls=Bar):
    obj = foo_cls.__new__(foo_cls)
    obj.f1 = 1
    obj.f2 = "str"
    obj.f3 = ["str1", None, "str2"]
    obj.f4 = {"k" + str(i): i for i in range(1, 7)}
    obj.f5 = create_bar(bar_cls)
    return obj


def create_bar(cls):
    obj = cls.__new__(cls)
    obj.f1 = 1
    obj.f2 = "str"
    return obj


@dataclass
class A:
    f1: "pa.int32"
    f2: Dict[str, str]


@cross_language_test
def test_map_encoder(data_file_path):
    encoder = pyfory.encoder(A)
    a = A(f1=1, f2={"pid": "12345", "ip": "0.0.0.0", "k1": "v1"})
    with open(data_file_path, "rb+") as f:
        data_bytes = f.read()
        obj = encoder.decode(data_bytes)
        debug_print("deserialized obj", obj)
        assert a == obj
        assert encoder.decode(encoder.encode(a)) == a
        f.seek(0)
        f.truncate()
        f.write(encoder.encode(a))


@cross_language_test
def test_encoder_without_schema(data_file_path):
    encoder = pyfory.encoder(FooPOJO)
    debug_print(encoder)
    foo = create_foo(foo_cls=FooPOJO, bar_cls=BarPOJO)
    with open(data_file_path, "rb+") as f:
        data_bytes = f.read()
        obj = encoder.decode(data_bytes)
        debug_print("deserialized foo", obj)
        assert foo == obj
        f.seek(0)
        f.truncate()
        f.write(encoder.encode(foo))


@cross_language_test
def test_serialization_without_schema(data_file_path, schema=None):
    schema = schema or create_foo_schema()
    encoder = pyfory.create_row_encoder(schema)
    foo = create_foo()
    with open(data_file_path, "rb+") as f:
        data_bytes = f.read()
        buf = pyfory.Buffer(data_bytes, 0, len(data_bytes))
        row = pyfory.RowData(schema, buf)
        debug_print("row", row)
        obj = encoder.from_row(row)
        debug_print("deserialized foo", obj)
        assert str(foo.f5) == str(obj.f5)
        # class of `f5` is generated, which may be different from class
        # of deserialized `f5`
        f5 = foo.f5
        foo.f5 = None
        obj.f5 = None
        # compare data using str instead of object to workaround different
        # class name
        assert str(obj) == str(foo)
        f.seek(0)
        f.truncate()
        foo.f5 = f5
        row = encoder.to_row(foo)
        f.write(row.to_bytes())


@cross_language_test
def test_serialization_with_schema(schema_file_path, data_file_path):
    with open(schema_file_path, "rb") as f:
        schema_bytes = f.read()
        schema = pa.ipc.read_schema(pa.py_buffer(schema_bytes))
        debug_print("deserialized schema", schema)
        test_serialization_without_schema(data_file_path, schema)


@cross_language_test
def test_record_batch_basic(data_file_path):
    with open(data_file_path, "rb") as f:
        record_batch_bytes = f.read()
        buf = pa.py_buffer(record_batch_bytes)
        reader = pa.ipc.open_stream(buf)
        batches = [batch for batch in reader]
        assert len(batches) == 1
        batch = batches[0]
        debug_print(f"batch {batch}")


@cross_language_test
def test_record_batch(data_file_path):
    with open(data_file_path, "rb") as f:
        record_batch_bytes = f.read()
        buf = pa.py_buffer(record_batch_bytes)
        reader = pa.ipc.open_stream(buf)
        foo_schema_without_meta = pa.schema(
            [pa.field(f.name, f.type, f.nullable) for f in create_foo_schema()]
        )
        assert reader.schema == foo_schema_without_meta
        # debug_print(f"reader.schema {reader.schema}")
        batches = [batch for batch in reader]
        assert len(batches) == 1
        batch = batches[0]
        # debug_print(f"batch[0] {batch[0]}")

        encoder = pyfory.create_row_encoder(create_foo_schema())
        writer = pyfory.ArrowWriter(create_foo_schema())
        num_rows = 128
        for i in range(num_rows):
            foo = create_foo()
            row = encoder.to_row(foo)
            writer.write(row)
        record_batch = writer.finish()
        assert batch == record_batch


@cross_language_test
def test_write_multi_record_batch(schema_file_path, data_file_path):
    with open(schema_file_path, "rb") as f:
        schema_bytes = f.read()
        schema = pa.ipc.read_schema(pa.py_buffer(schema_bytes))
    with open(data_file_path, "rb") as f:
        data_bytes = f.read()
        pa.ipc.read_record_batch(pa.py_buffer(data_bytes), schema)


@cross_language_test
def test_buffer(data_file_path):
    with open(data_file_path, "rb") as f:
        data_bytes = f.read()
        buffer = pyfory.Buffer(data_bytes)
        assert buffer.read_bool() is True
        assert buffer.read_int8() == 2**7 - 1
        assert buffer.read_int16() == 2**15 - 1
        assert buffer.read_int32() == 2**31 - 1
        assert buffer.read_int64() == 2**63 - 1
        assert math.isclose(buffer.read_float(), -1.1, rel_tol=1e-03)
        assert math.isclose(buffer.read_double(), -1.1, rel_tol=1e-03)
        assert buffer.read_varuint32() == 100
        binary = b"ab"
        assert buffer.read_bytes(buffer.read_int32()) == binary
        buffer.write_bool(True)
        buffer.write_int8(2**7 - 1)
        buffer.write_int16(2**15 - 1)
        buffer.write_int32(2**31 - 1)
        buffer.write_int64(2**63 - 1)
        buffer.write_float(-1.1)
        buffer.write_double(-1.1)
        buffer.write_varuint32(100)
        buffer.write_int32(len(binary))
        buffer.write_bytes(binary)
    with open(data_file_path, "wb+") as f:
        f.write(buffer.get_bytes(0, buffer.writer_index))


@cross_language_test
def test_murmurhash3(data_file_path):
    with open(data_file_path, "rb") as f:
        data_bytes = f.read()
        buffer = pyfory.Buffer(data_bytes)
        h1, h2 = pyfory.lib.mmh3.hash_buffer(bytearray([1, 2, 8]), seed=47)
        assert buffer.read_int64() == h1
        assert buffer.read_int64() == h2


@cross_language_test
def test_cross_language_serializer(data_file_path):
    with open(data_file_path, "rb") as f:
        data_bytes = f.read()
        buffer = pyfory.Buffer(data_bytes)
        fory = pyfory.Fory(language=pyfory.Language.XLANG, ref_tracking=True)
        objects = []
        assert _deserialize_and_append(fory, buffer, objects) is True
        assert _deserialize_and_append(fory, buffer, objects) is False
        assert _deserialize_and_append(fory, buffer, objects) == -1
        assert _deserialize_and_append(fory, buffer, objects) == 2**7 - 1
        assert _deserialize_and_append(fory, buffer, objects) == -(2**7)
        assert _deserialize_and_append(fory, buffer, objects) == 2**15 - 1
        assert _deserialize_and_append(fory, buffer, objects) == -(2**15)
        assert _deserialize_and_append(fory, buffer, objects) == 2**31 - 1
        assert _deserialize_and_append(fory, buffer, objects) == -(2**31)
        x = _deserialize_and_append(fory, buffer, objects)
        assert x == 2**63 - 1, x
        assert _deserialize_and_append(fory, buffer, objects) == -(2**63)
        assert _deserialize_and_append(fory, buffer, objects) == -1.0
        assert _deserialize_and_append(fory, buffer, objects) == -1.0
        assert _deserialize_and_append(fory, buffer, objects) == "str"
        day = datetime.date(2021, 11, 23)
        assert _deserialize_and_append(fory, buffer, objects) == day
        instant = datetime.datetime.fromtimestamp(100)
        assert _deserialize_and_append(fory, buffer, objects) == instant
        list_ = ["a", 1, -1.0, instant, day]
        assert _deserialize_and_append(fory, buffer, objects) == list_
        dict_ = {f"k{i}": v for i, v in enumerate(list_)}
        dict_.update({v: v for v in list_})
        assert _deserialize_and_append(fory, buffer, objects) == dict_
        set_ = set(list_)
        assert _deserialize_and_append(fory, buffer, objects) == set_

        # test primitive arrays
        import numpy as np

        np.testing.assert_array_equal(
            _deserialize_and_append(fory, buffer, objects),
            np.array([True, False], dtype=np.bool_),
        )
        np.testing.assert_array_equal(
            _deserialize_and_append(fory, buffer, objects),
            np.array([1, 2**15 - 1], dtype=np.int16),
        )
        np.testing.assert_array_equal(
            _deserialize_and_append(fory, buffer, objects),
            np.array([1, 2**31 - 1], dtype=np.int32),
        )
        np.testing.assert_array_equal(
            _deserialize_and_append(fory, buffer, objects),
            np.array([1, 2**63 - 1], dtype=np.int64),
        )
        np.testing.assert_array_equal(
            _deserialize_and_append(fory, buffer, objects),
            np.array([1, 2], dtype=np.float32),
        )
        np.testing.assert_array_equal(
            _deserialize_and_append(fory, buffer, objects),
            np.array([1, 2], dtype=np.float64),
        )
        new_buf = pyfory.Buffer.allocate(32)
        for obj in objects:
            fory.serialize(obj, buffer=new_buf)
    with open(data_file_path, "wb+") as f:
        f.write(new_buf.get_bytes(0, new_buf.writer_index))


@cross_language_test
def _deserialize_and_append(fory, buffer, objects: list):
    obj = fory.deserialize(buffer)
    objects.append(obj)
    return obj


@cross_language_test
def test_cross_language_reference(data_file_path):
    with open(data_file_path, "rb") as f:
        data_bytes = f.read()
        buffer = pyfory.Buffer(data_bytes)
        fory = pyfory.Fory(language=pyfory.Language.XLANG, ref_tracking=True)
        new_list = fory.deserialize(buffer)
        assert new_list[0] is new_list
        new_map = new_list[1]
        assert new_map["k1"] is new_map
        assert new_map["k2"] is new_list

        new_list2 = fory.deserialize(fory.serialize(new_list))
        assert new_list2[0] is new_list2
        new_map = new_list2[1]
        assert new_map["k1"] is new_map
        assert new_map["k2"] is new_list2

        new_buf = pyfory.Buffer.allocate(32)
        fory.serialize(new_list, buffer=new_buf)
    with open(data_file_path, "wb+") as f:
        f.write(new_buf.get_bytes(0, new_buf.writer_index))


@cross_language_test
def test_serialize_arrow_in_band(data_file_path):
    with open(data_file_path, "rb") as f:
        batch = create_record_batch(2000)
        table = pa.Table.from_batches([batch] * 2)
        data_bytes = f.read()
        buffer = pyfory.Buffer(data_bytes)
        fory = pyfory.Fory(language=pyfory.Language.XLANG, ref_tracking=True)
        new_batch = fory.deserialize(buffer)
        assert new_batch == batch
        new_table = fory.deserialize(buffer)
        assert table == new_table


@cross_language_test
def test_serialize_arrow_out_of_band(int_band_file, out_of_band_file):
    with open(int_band_file, "rb") as f:
        in_band_data_bytes = f.read()
    with open(out_of_band_file, "rb") as f:
        out_of_band_data_bytes = f.read()
    batch = create_record_batch(2000)
    table = pa.Table.from_batches([batch] * 2)
    in_band_buffer = pyfory.Buffer(in_band_data_bytes)
    out_of_band_buffer = pyfory.Buffer(out_of_band_data_bytes)
    len1, len2 = out_of_band_buffer.read_int32(), out_of_band_buffer.read_int32()
    buffers = [
        out_of_band_buffer.slice(8, len1),
        out_of_band_buffer.slice(8 + len1, len2),
    ]
    fory = pyfory.Fory(language=pyfory.Language.XLANG, ref_tracking=True)
    objects = fory.deserialize(in_band_buffer, buffers=buffers)
    assert objects == [batch, table]
    buffer_objects = []
    in_band_buffer = fory.serialize(
        [batch, table], buffer_callback=buffer_objects.append
    )
    buffers = [o.to_buffer() for o in buffer_objects]
    with open(int_band_file, "wb+") as f:
        f.write(in_band_buffer)
    with open(out_of_band_file, "wb+") as f:
        size_buf = pyfory.Buffer.allocate(8)
        size_buf.write_int32(len(buffers[0]))
        size_buf.write_int32(len(buffers[1]))
        f.write(size_buf)
        f.write(buffers[0])
        f.write(buffers[1])


@cross_language_test
def create_record_batch(size):
    data = [
        pa.array([bool(i % 2) for i in range(size)]),
        pa.array([f"test{i}" for i in range(size)]),
    ]
    return pa.RecordBatch.from_arrays(data, ["boolean", "varchar"])


@dataclass
class ComplexObject1:
    f1: Any = None
    f2: str = None
    f3: List[str] = None
    f4: Dict[pyfory.Int8Type, pyfory.Int32Type] = None
    f5: pyfory.Int8Type = None
    f6: pyfory.Int16Type = None
    f7: pyfory.Int32Type = None
    f8: pyfory.Int64Type = None
    f9: pyfory.Float32Type = None
    f10: pyfory.Float64Type = None
    f11: pyfory.Int16ArrayType = None
    f12: List[pyfory.Int16Type] = None


@dataclass
class ComplexObject2:
    f1: Any
    f2: Dict[pyfory.Int8Type, pyfory.Int32Type]


def test_serialize_simple_struct_local():
    fory = pyfory.Fory(language=pyfory.Language.XLANG, ref_tracking=True)
    fory.register_type(ComplexObject2, namespace="test", typename="ComplexObject2")
    obj = ComplexObject2(f1=True, f2={-1: 2})
    new_buf = fory.serialize(obj)
    assert fory.deserialize(new_buf) == obj


@cross_language_test
def test_serialize_simple_struct(data_file_path):
    fory = pyfory.Fory(language=pyfory.Language.XLANG, ref_tracking=True)
    fory.register_type(ComplexObject2, namespace="test", typename="ComplexObject2")
    obj = ComplexObject2(f1=True, f2={-1: 2})
    struct_round_back(data_file_path, fory, obj)


class SomeClass:
    f1: "SomeClass"
    f2: Dict[str, str]
    f3: Dict[str, str]


def test_custom_class_roundtrip():
    fory = pyfory.Fory(ref_tracking=True)
    fory.register_type(SomeClass, typename="example.SomeClass")
    obj1 = SomeClass()
    obj1.f2 = {"k1": "v1", "k2": "v2"}
    obj1.f1, obj1.f3 = obj1, obj1.f2
    data1 = fory.serialize(obj1)
    obj2 = fory.deserialize(data1)
    data2 = fory.serialize(obj2)
    assert data1 == data2
    # bytes can be data serialized by other languages.
    # due to the self-referential nature of this object,
    # direct `==` comparison will fail.
    # 1. Serialize `obj1` to `data1`
    # 2. Deserialize `data1` to `obj2`
    # 3. Serialize `obj2` to `data2`
    # If `data1 == data2`, the round-trip preserves value equivalence.
    # print(data1)
    # print(data2)
    # print(obj1)
    # print(obj2)


class EnumTestClass(enum.Enum):
    FOO = 0
    BAR = 1


@dataclass
class EnumFieldStruct:
    f1: EnumTestClass
    f2: EnumTestClass
    f3: str


@cross_language_test
def test_enum_field(data_file_path):
    fory = pyfory.Fory(language=pyfory.Language.XLANG, ref_tracking=False)
    fory.register_type(EnumTestClass, namespace="test", typename="EnumTestClass")
    fory.register_type(EnumFieldStruct, namespace="test", typename="EnumFieldStruct")
    obj = EnumFieldStruct(f1=EnumTestClass.FOO, f2=EnumTestClass.BAR, f3="abc")
    struct_round_back(data_file_path, fory, obj)


@cross_language_test
def test_struct_hash(data_file_path):
    with open(data_file_path, "rb") as f:
        data_bytes = f.read()
    debug_print(f"len {len(data_bytes)}")
    read_hash = pyfory.Buffer(data_bytes).read_int32()
    fory = pyfory.Fory(language=pyfory.Language.XLANG, ref_tracking=True)
    fory.register_type(ComplexObject1, typename="ComplexObject1")
    serializer = fory.type_resolver.get_serializer(ComplexObject1)
    from pyfory._struct import _get_hash

    v = _get_hash(fory, serializer._field_names, serializer._type_hints)
    assert read_hash == v, (read_hash, v)


@cross_language_test
def test_serialize_complex_struct(data_file_path):
    fory = pyfory.Fory(language=pyfory.Language.XLANG, ref_tracking=True)
    fory.register_type(ComplexObject1, namespace="test", typename="ComplexObject1")
    fory.register_type(ComplexObject2, namespace="test", typename="ComplexObject2")

    obj2 = ComplexObject2(f1=True, f2={-1: 2})
    obj1 = ComplexObject1(
        f1=obj2,
        f2="abc",
        f3=["abc", "abc"],
        f4={1: 2},
        f5=2**7 - 1,
        f6=2**15 - 1,
        f7=2**31 - 1,
        f8=2**63 - 1,
        f9=1.0 / 2,
        f10=1 / 3.0,
        f11=array.array("h", [1, 2]),
        f12=[-1, 4],
    )
    struct_round_back(data_file_path, fory, obj1)


def struct_round_back(data_file_path, fory, obj1):
    # new_buf = fory.serialize(obj1)
    # assert fory.deserialize(new_buf) == obj1
    with open(data_file_path, "rb") as f:
        data_bytes = f.read()
    debug_print(f"len {len(data_bytes)}")
    new_obj = fory.deserialize(data_bytes)
    debug_print(new_obj)
    assert new_obj == obj1, f"new_obj {new_obj}\n expected {obj1}"
    new_buf = fory.serialize(new_obj)
    debug_print(f"new_buf size {len(new_buf)}")
    assert fory.deserialize(new_buf) == new_obj
    with open(data_file_path, "wb+") as f:
        f.write(new_buf)


class ComplexObject1Serializer(pyfory.serializer.Serializer):
    def write(self, buffer, value):
        self.xwrite(buffer, value)

    def read(self, buffer):
        return self.xread(buffer)

    def xwrite(self, buffer, value):
        self.fory.xserialize_ref(buffer, value.f1)
        self.fory.xserialize_ref(buffer, value.f2)
        self.fory.xserialize_ref(buffer, value.f3)

    def xread(self, buffer):
        obj = ComplexObject1(
            *([None] * len(typing.get_type_hints(ComplexObject1).keys()))
        )
        self.fory.ref_resolver.reference(obj)
        obj.f1 = self.fory.xdeserialize_ref(buffer)
        obj.f2 = self.fory.xdeserialize_ref(buffer)
        obj.f3 = self.fory.xdeserialize_ref(buffer)
        return obj


@cross_language_test
def test_register_serializer(data_file_path):
    with open(data_file_path, "rb") as f:
        data_bytes = f.read()
    buffer = pyfory.Buffer(data_bytes)

    fory = pyfory.Fory(language=pyfory.Language.XLANG, ref_tracking=True)
    fory.register_type(
        ComplexObject1,
        typename="test.ComplexObject1",
        serializer=ComplexObject1Serializer(fory, ComplexObject1),
    )
    expected = ComplexObject1(*[None] * 12)
    expected.f1, expected.f2, expected.f3 = True, "abc", ["abc", "abc"]
    bytes1 = fory.serialize(expected)
    assert fory.deserialize(bytes1) == expected
    new_obj = fory.deserialize(buffer)

    debug_print(new_obj)
    assert new_obj == expected
    new_buf = pyfory.Buffer.allocate(32)
    fory.serialize(new_obj, buffer=new_buf)
    bytes1 = fory.serialize(new_obj)
    assert len(bytes1) == len(data_bytes)
    # header can be different to embed writer info like language
    assert bytes1[8:] == data_bytes[8:]
    assert fory.deserialize(fory.serialize(new_obj)) == new_obj, new_obj
    print(f"test_register_serializer: {new_obj}")
    with open(data_file_path, "wb+") as f:
        f.write(new_buf.get_bytes(0, new_buf.writer_index))


@cross_language_test
def test_oob_buffer(in_band_file_path, out_of_band_file_path):
    with open(in_band_file_path, "rb") as f:
        in_band_bytes = f.read()
    with open(out_of_band_file_path, "rb") as f:
        out_of_band_buffer = pyfory.Buffer(f.read())
    fory = pyfory.Fory(language=pyfory.Language.XLANG, ref_tracking=True)
    n_buffers = out_of_band_buffer.read_int32()
    buffers = []
    for i in range(n_buffers):
        length = out_of_band_buffer.read_int32()
        reader_index = out_of_band_buffer.reader_index
        buffers.append(out_of_band_buffer.slice(reader_index, length))
        out_of_band_buffer.reader_index += length
    new_obj = fory.deserialize(in_band_bytes, buffers)
    obj = [bytes(bytearray([0, 1])) for _ in range(10)]
    assert new_obj == obj, (obj, new_obj)

    buffer_objects = []
    counter = 0

    def buffer_callback(binary_object):
        nonlocal counter
        counter += 1
        if counter % 2 == 0:
            buffer_objects.append(binary_object)
            return False
        else:
            return True

    serialized = fory.serialize(obj, buffer_callback=buffer_callback)
    # in_band_bytes size may be different because it may contain language-specific meta.
    debug_print(f"{len(serialized), len(in_band_bytes)}")
    debug_print(f"deserialized from other language {new_obj}")
    debug_print(
        f"deserialized from python "
        f"{fory.deserialize(serialized, [o.to_buffer() for o in buffer_objects])}"
    )
    fory.deserialize(serialized, [o.to_buffer() for o in buffer_objects])
    with open(in_band_file_path, "wb+") as f:
        f.write(serialized)
    out_of_band_buffer.write_int32(len(buffer_objects))
    for buffer_object in buffer_objects:
        out_of_band_buffer.write_int32(buffer_object.total_bytes())
        buffer_object.write_to(out_of_band_buffer)
    with open(out_of_band_file_path, "wb+") as f:
        f.write(out_of_band_buffer.to_bytes(0, out_of_band_buffer.writer_index))


if __name__ == "__main__":
    import sys

    print(f"Execute {sys.argv}")
    try:
        args = sys.argv[1:]
        assert len(args) > 0
        func = getattr(sys.modules[__name__], args[0])
        if not func:
            raise Exception("Unknown args {}".format(args))
        func(*args[1:])
    except BaseException as e:
        logging.exception("Execute %s failed with %s", args, e)
        raise
