"""
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
"""
import calendar
import datetime
import json
import uuid
import math
from collections import OrderedDict
from decimal import *
import logging
from datetime import timedelta

import six
from aenum import Enum
from isodate import parse_duration, duration_isoformat

from gremlin_python import statics
from gremlin_python.statics import FloatType, FunctionType, IntType, LongType, TypeType, DictType, ListType, SetType, SingleByte, ByteBufferType, SingleChar
from gremlin_python.process.traversal import Binding, Bytecode, Direction, P, TextP, Traversal, Traverser, TraversalStrategy, T
from gremlin_python.structure.graph import Edge, Property, Vertex, VertexProperty, Path

log = logging.getLogger(__name__)

# When we fall back to a superclass's serializer, we iterate over this map.
# We want that iteration order to be consistent, so we use an OrderedDict,
# not a dict.
_serializers = OrderedDict()
_deserializers = {}


class GraphSONTypeType(type):
    def __new__(mcs, name, bases, dct):
        cls = super(GraphSONTypeType, mcs).__new__(mcs, name, bases, dct)
        if not name.startswith('_'):
            if cls.python_type:
                _serializers[cls.python_type] = cls
            if cls.graphson_type:
                _deserializers[cls.graphson_type] = cls
        return cls


class GraphSONUtil(object):
    TYPE_KEY = "@type"
    VALUE_KEY = "@value"

    @classmethod
    def typedValue(cls, type_name, value, prefix="g"):
        out = {cls.TYPE_KEY: cls.formatType(prefix, type_name)}
        if value is not None:
            out[cls.VALUE_KEY] = value
        return out

    @classmethod
    def formatType(cls, prefix, type_name):
        return "%s:%s" % (prefix, type_name)


# Read/Write classes split to follow precedence of the Java API
class GraphSONWriter(object):
    def __init__(self, serializer_map=None):
        """
        :param serializer_map: map from Python type to serializer instance implementing `dictify`
        """
        self.serializers = _serializers.copy()
        if serializer_map:
            self.serializers.update(serializer_map)

    def writeObject(self, objectData):
        # to JSON
        return json.dumps(self.toDict(objectData), separators=(',', ':'))

    def toDict(self, obj):
        """
        Encodes python objects in GraphSON type-tagged dict values
        """
        try:
            return self.serializers[type(obj)].dictify(obj, self)
        except KeyError:
            for key, serializer in self.serializers.items():
                if isinstance(obj, key):
                    return serializer.dictify(obj, self)

        if isinstance(obj, dict):
            return dict((self.toDict(k), self.toDict(v)) for k, v in obj.items())
        elif isinstance(obj, set):
            return set([self.toDict(o) for o in obj])
        elif isinstance(obj, list):
            return [self.toDict(o) for o in obj]
        else:
            return obj


class GraphSONReader(object):
    def __init__(self, deserializer_map=None):
        """
        :param deserializer_map: map from GraphSON type tag to deserializer instance implementing `objectify`
        """
        self.deserializers = _deserializers.copy()
        if deserializer_map:
            self.deserializers.update(deserializer_map)

    def readObject(self, jsonData):
        # from JSON
        return self.toObject(json.loads(jsonData))

    def toObject(self, obj):
        """
        Unpacks GraphSON type-tagged dict values into objects mapped in self.deserializers
        """
        if isinstance(obj, dict):
            try:
                return self.deserializers[obj[GraphSONUtil.TYPE_KEY]].objectify(obj[GraphSONUtil.VALUE_KEY], self)
            except KeyError:
                pass
            return dict((self.toObject(k), self.toObject(v)) for k, v in obj.items())
        elif isinstance(obj, set):
            return set([self.toObject(o) for o in obj])
        elif isinstance(obj, list):
            return [self.toObject(o) for o in obj]
        else:
            return obj


@six.add_metaclass(GraphSONTypeType)
class _GraphSONTypeIO(object):
    python_type = None
    graphson_type = None

    symbolMap = {"global_": "global", "as_": "as", "in_": "in", "and_": "and",
                 "or_": "or", "is_": "is", "not_": "not", "from_": "from",
                 "set_": "set", "list_": "list", "all_": "all", "with_": "with",
                 "filter_": "filter", "id_": "id", "max_": "max", "min_": "min", "sum_": "sum"}

    @classmethod
    def unmangleKeyword(cls, symbol):
        return cls.symbolMap.get(symbol, symbol)

    def dictify(self, obj, writer):
        raise NotImplementedError()

    def objectify(self, d, reader):
        raise NotImplementedError()


class _BytecodeSerializer(_GraphSONTypeIO):
    @classmethod
    def _dictify_instructions(cls, instructions, writer):
        out = []
        for instruction in instructions:
            inst = [instruction[0]]
            inst.extend(writer.toDict(arg) for arg in instruction[1:])
            out.append(inst)
        return out

    @classmethod
    def dictify(cls, bytecode, writer):
        if isinstance(bytecode, Traversal):
            bytecode = bytecode.bytecode
        out = {}
        if bytecode.source_instructions:
            out["source"] = cls._dictify_instructions(bytecode.source_instructions, writer)
        if bytecode.step_instructions:
            out["step"] = cls._dictify_instructions(bytecode.step_instructions, writer)
        return GraphSONUtil.typedValue("Bytecode", out)


class TraversalSerializer(_BytecodeSerializer):
    python_type = Traversal


class BytecodeSerializer(_BytecodeSerializer):
    python_type = Bytecode


class VertexSerializer(_GraphSONTypeIO):
    python_type = Vertex
    graphson_type = "g:Vertex"

    @classmethod
    def dictify(cls, vertex, writer):
        return GraphSONUtil.typedValue("Vertex", {"id": writer.toDict(vertex.id),
                                                  "label": writer.toDict(vertex.label)})


class EdgeSerializer(_GraphSONTypeIO):
    python_type = Edge
    graphson_type = "g:Edge"

    @classmethod
    def dictify(cls, edge, writer):
        return GraphSONUtil.typedValue("Edge", {"id": writer.toDict(edge.id),
                                                "outV": writer.toDict(edge.outV.id),
                                                "outVLabel": writer.toDict(edge.outV.label),
                                                "label": writer.toDict(edge.label),
                                                "inV": writer.toDict(edge.inV.id),
                                                "inVLabel": writer.toDict(edge.inV.label)})


class VertexPropertySerializer(_GraphSONTypeIO):
    python_type = VertexProperty
    graphson_type = "g:VertexProperty"

    @classmethod
    def dictify(cls, vertex_property, writer):
        return GraphSONUtil.typedValue("VertexProperty", {"id": writer.toDict(vertex_property.id),
                                                          "label": writer.toDict(vertex_property.label),
                                                          "value": writer.toDict(vertex_property.value),
                                                          "vertex": writer.toDict(vertex_property.vertex.id)})


class PropertySerializer(_GraphSONTypeIO):
    python_type = Property
    graphson_type = "g:Property"

    @classmethod
    def dictify(cls, property, writer):
        elementDict = writer.toDict(property.element)
        if elementDict is not None:
            valueDict = elementDict["@value"]
            if "outVLabel" in valueDict:
                del valueDict["outVLabel"]
            if "inVLabel" in valueDict:
                del valueDict["inVLabel"]
            if "properties" in valueDict:
                del valueDict["properties"]
            if "value" in valueDict:
                del valueDict["value"]
        return GraphSONUtil.typedValue("Property", {"key": writer.toDict(property.key),
                                                    "value": writer.toDict(property.value),
                                                    "element": elementDict})


class TraversalStrategySerializer(_GraphSONTypeIO):
    python_type = TraversalStrategy

    @classmethod
    def dictify(cls, strategy, writer):
        configuration = {}
        for key in strategy.configuration:
            configuration[key] = writer.toDict(strategy.configuration[key])
        return GraphSONUtil.typedValue(strategy.strategy_name, configuration)


class TraverserIO(_GraphSONTypeIO):
    python_type = Traverser
    graphson_type = "g:Traverser"

    @classmethod
    def dictify(cls, traverser, writer):
        return GraphSONUtil.typedValue("Traverser", {"value": writer.toDict(traverser.object),
                                                     "bulk": writer.toDict(traverser.bulk)})

    @classmethod
    def objectify(cls, d, reader):
        return Traverser(reader.toObject(d["value"]),
                         reader.toObject(d["bulk"]))


class EnumSerializer(_GraphSONTypeIO):
    python_type = Enum

    @classmethod
    def dictify(cls, enum, _):
        return GraphSONUtil.typedValue(cls.unmangleKeyword(type(enum).__name__),
                                       cls.unmangleKeyword(str(enum.name)))


class PSerializer(_GraphSONTypeIO):
    python_type = P

    @classmethod
    def dictify(cls, p, writer):
        out = {"predicate": p.operator,
               "value": [writer.toDict(p.value), writer.toDict(p.other)] if p.other is not None else
               writer.toDict(p.value)}
        return GraphSONUtil.typedValue("P", out)


class TextPSerializer(_GraphSONTypeIO):
    python_type = TextP

    @classmethod
    def dictify(cls, p, writer):
        out = {"predicate": p.operator,
               "value": [writer.toDict(p.value), writer.toDict(p.other)] if p.other is not None else
               writer.toDict(p.value)}
        return GraphSONUtil.typedValue("TextP", out)


class BindingSerializer(_GraphSONTypeIO):
    python_type = Binding

    @classmethod
    def dictify(cls, binding, writer):
        out = {"key": binding.key,
               "value": writer.toDict(binding.value)}
        return GraphSONUtil.typedValue("Binding", out)


class LambdaSerializer(_GraphSONTypeIO):
    python_type = FunctionType

    @classmethod
    def dictify(cls, lambda_object, writer):
        lambda_result = lambda_object()
        script = lambda_result if isinstance(lambda_result, str) else lambda_result[0]
        language = statics.default_lambda_language if isinstance(lambda_result, str) else lambda_result[1]
        out = {"script": script,
               "language": language}
        if language == "gremlin-jython" or language == "gremlin-python":
            if not script.strip().startswith("lambda"):
                script = "lambda " + script
                out["script"] = script
            out["arguments"] = six.get_function_code(eval(out["script"])).co_argcount
        elif language == "gremlin-groovy" and "->" in script:
            # if the user has explicitly added parameters to the groovy closure then we can easily detect one or two
            # arg lambdas - if we can't detect 1 or 2 then we just go with "unknown"
            args = script[0:script.find("->")]
            out["arguments"] = 2 if "," in args else 1
        else:
            out["arguments"] = -1

        return GraphSONUtil.typedValue("Lambda", out)


class TypeSerializer(_GraphSONTypeIO):
    python_type = TypeType

    @classmethod
    def dictify(cls, typ, writer):
        return writer.toDict(typ())


class UUIDIO(_GraphSONTypeIO):
    python_type = uuid.UUID
    graphson_type = "g:UUID"
    graphson_base_type = "UUID"

    @classmethod
    def dictify(cls, obj, writer):
        return GraphSONUtil.typedValue(cls.graphson_base_type, str(obj))

    @classmethod
    def objectify(cls, d, reader):
        return cls.python_type(d)


class DateIO(_GraphSONTypeIO):
    python_type = datetime.datetime
    graphson_type = "g:Date"
    graphson_base_type = "Date"

    @classmethod
    def dictify(cls, obj, writer):
        try:
            timestamp_seconds = calendar.timegm(obj.utctimetuple())
            pts = timestamp_seconds * 1e3 + getattr(obj, 'microsecond', 0) / 1e3
        except AttributeError:
            pts = calendar.timegm(obj.timetuple()) * 1e3

        ts = int(round(pts))
        return GraphSONUtil.typedValue(cls.graphson_base_type, ts)

    @classmethod
    def objectify(cls, ts, reader):
        # Python timestamp expects seconds
        return datetime.datetime.utcfromtimestamp(ts / 1000.0)


# Based on current implementation, this class must always be declared before FloatIO.
# Seems pretty fragile for future maintainers. Maybe look into this.
class TimestampIO(_GraphSONTypeIO):
    """A timestamp in Python is type float"""
    python_type = statics.timestamp
    graphson_type = "g:Timestamp"
    graphson_base_type = "Timestamp"

    @classmethod
    def dictify(cls, obj, writer):
        # Java timestamp expects milliseconds integer
        # Have to use int because of legacy Python
        ts = int(round(obj * 1000))
        return GraphSONUtil.typedValue(cls.graphson_base_type, ts)

    @classmethod
    def objectify(cls, ts, reader):
        # Python timestamp expects seconds
        return cls.python_type(ts / 1000.0)


class _NumberIO(_GraphSONTypeIO):
    @classmethod
    def dictify(cls, n, writer):
        if isinstance(n, bool):  # because isinstance(False, int) and isinstance(True, int)
            return n
        return GraphSONUtil.typedValue(cls.graphson_base_type, n)

    @classmethod
    def objectify(cls, v, _):
        return cls.python_type(v)


class ListIO(_GraphSONTypeIO):
    python_type = ListType
    graphson_type = "g:List"

    @classmethod
    def dictify(cls, l, writer):
        new_list = []
        for obj in l:
            new_list.append(writer.toDict(obj))
        return GraphSONUtil.typedValue("List", new_list)

    @classmethod
    def objectify(cls, l, reader):
        new_list = []
        for obj in l:
            new_list.append(reader.toObject(obj))
        return new_list


class SetIO(_GraphSONTypeIO):
    python_type = SetType
    graphson_type = "g:Set"

    @classmethod
    def dictify(cls, s, writer):
        new_list = []
        for obj in s:
            new_list.append(writer.toDict(obj))
        return GraphSONUtil.typedValue("Set", new_list)

    @classmethod
    def objectify(cls, s, reader):
        """
        By default, returns a python set

        In case Java returns numeric values of different types which
        python don't recognize, coerce and return a list.
        See comments of TINKERPOP-1844 for more details
        """
        new_list = [reader.toObject(obj) for obj in s]
        new_set = set(new_list)
        if len(new_list) != len(new_set):
            log.warning("Coercing g:Set to list due to java numeric values. "
                        "See TINKERPOP-1844 for more details.")
            return new_list

        return new_set


class MapType(_GraphSONTypeIO):
    python_type = DictType
    graphson_type = "g:Map"

    @classmethod
    def dictify(cls, d, writer):
        l = []
        for key in d:
            l.append(writer.toDict(key))
            l.append(writer.toDict(d[key]))
        return GraphSONUtil.typedValue("Map", l)

    @classmethod
    def objectify(cls, l, reader):
        new_dict = {}
        if len(l) > 0:
            x = 0
            while x < len(l):
                new_dict[reader.toObject(l[x])] = reader.toObject(l[x + 1])
                x = x + 2
        return new_dict


class BulkSetIO(_GraphSONTypeIO):
    graphson_type = "g:BulkSet"

    @classmethod
    def objectify(cls, l, reader):
        new_list = []

        # this approach basically mimics what currently existed in 3.3.4 and prior versions where BulkSet is
        # basically just coerced to list. the limitation here is that if the value of a bulk exceeds the size of
        # a list (into the long space) then stuff won't work nice.
        if len(l) > 0:
            x = 0
            while x < len(l):
                obj = reader.toObject(l[x])
                bulk = reader.toObject(l[x + 1])
                for y in range(bulk):
                    new_list.append(obj)
                x = x + 2
        return new_list


class FloatIO(_NumberIO):
    python_type = FloatType
    graphson_type = "g:Float"
    graphson_base_type = "Float"

    @classmethod
    def dictify(cls, n, writer):
        if isinstance(n, bool):  # because isinstance(False, int) and isinstance(True, int)
            return n
        elif math.isnan(n):
            return GraphSONUtil.typedValue(cls.graphson_base_type, "NaN")
        elif math.isinf(n) and n > 0:
            return GraphSONUtil.typedValue(cls.graphson_base_type, "Infinity")
        elif math.isinf(n) and n < 0:
            return GraphSONUtil.typedValue(cls.graphson_base_type, "-Infinity")
        else:
            return GraphSONUtil.typedValue(cls.graphson_base_type, n)

    @classmethod
    def objectify(cls, v, _):
        if isinstance(v, str):
            if v == 'NaN':
                return float('nan')
            elif v == "Infinity":
                return float('inf')
            elif v == "-Infinity":
                return float('-inf')

        return cls.python_type(v)


class BigDecimalIO(_NumberIO):
    python_type = Decimal
    graphson_type = "gx:BigDecimal"
    graphson_base_type = "BigDecimal"

    @classmethod
    def dictify(cls, n, writer):
        if isinstance(n, bool):  # because isinstance(False, int) and isinstance(True, int)
            return n
        elif math.isnan(n):
            return GraphSONUtil.typedValue(cls.graphson_base_type, "NaN", "gx")
        elif math.isinf(n) and n > 0:
            return GraphSONUtil.typedValue(cls.graphson_base_type, "Infinity", "gx")
        elif math.isinf(n) and n < 0:
            return GraphSONUtil.typedValue(cls.graphson_base_type, "-Infinity", "gx")
        else:
            return GraphSONUtil.typedValue(cls.graphson_base_type, str(n), "gx")

    @classmethod
    def objectify(cls, v, _):
        if isinstance(v, str):
            if v == 'NaN':
                return Decimal('nan')
            elif v == "Infinity":
                return Decimal('inf')
            elif v == "-Infinity":
                return Decimal('-inf')

        return Decimal(v)


class DoubleIO(FloatIO):
    graphson_type = "g:Double"
    graphson_base_type = "Double"


class Int64IO(_NumberIO):
    python_type = LongType
    graphson_type = "g:Int64"
    graphson_base_type = "Int64"

    @classmethod
    def dictify(cls, n, writer):
        # if we exceed Java long range then we need a BigInteger
        if isinstance(n, bool):
            return n
        elif n < -9223372036854775808 or n > 9223372036854775807:
            return GraphSONUtil.typedValue("BigInteger", str(n), "gx")
        else:
            return GraphSONUtil.typedValue(cls.graphson_base_type, n)


class BigIntegerIO(Int64IO):
    graphson_type = "gx:BigInteger"


class Int32IO(Int64IO):
    python_type = IntType
    graphson_type = "g:Int32"
    graphson_base_type = "Int32"


class ByteIO(_NumberIO):
    python_type = SingleByte
    graphson_type = "gx:Byte"
    graphson_base_type = "Byte"

    @classmethod
    def dictify(cls, n, writer):
        if isinstance(n, bool):  # because isinstance(False, int) and isinstance(True, int)
            return n
        return GraphSONUtil.typedValue(cls.graphson_base_type, n, "gx")

    @classmethod
    def objectify(cls, v, _):
        return int.__new__(SingleByte, v)


class ByteBufferIO(_GraphSONTypeIO):
    python_type = ByteBufferType
    graphson_type = "gx:ByteBuffer"
    graphson_base_type = "ByteBuffer"

    @classmethod
    def dictify(cls, n, writer):
        return GraphSONUtil.typedValue(cls.graphson_base_type, "".join(chr(x) for x in n), "gx")

    @classmethod
    def objectify(cls, v, _):
        return cls.python_type(v, "utf8")


class CharIO(_GraphSONTypeIO):
    python_type = SingleChar
    graphson_type = "gx:Char"
    graphson_base_type = "Char"

    @classmethod
    def dictify(cls, n, writer):
        return GraphSONUtil.typedValue(cls.graphson_base_type, n, "gx")

    @classmethod
    def objectify(cls, v, _):
        return str.__new__(SingleChar, v)


class DurationIO(_GraphSONTypeIO):
    python_type = timedelta
    graphson_type = "gx:Duration"
    graphson_base_type = "Duration"

    @classmethod
    def dictify(cls, n, writer):
        return GraphSONUtil.typedValue(cls.graphson_base_type, duration_isoformat(n), "gx")

    @classmethod
    def objectify(cls, v, _):
        return parse_duration(v)


class VertexDeserializer(_GraphSONTypeIO):
    graphson_type = "g:Vertex"

    @classmethod
    def objectify(cls, d, reader):
        return Vertex(reader.toObject(d["id"]), d.get("label", "vertex"))


class EdgeDeserializer(_GraphSONTypeIO):
    graphson_type = "g:Edge"

    @classmethod
    def objectify(cls, d, reader):
        return Edge(reader.toObject(d["id"]),
                    Vertex(reader.toObject(d["outV"]), d.get("outVLabel", "vertex")),
                    d.get("label", "edge"),
                    Vertex(reader.toObject(d["inV"]), d.get("inVLabel", "vertex")))


class VertexPropertyDeserializer(_GraphSONTypeIO):
    graphson_type = "g:VertexProperty"

    @classmethod
    def objectify(cls, d, reader):
        vertex = Vertex(reader.toObject(d.get("vertex"))) if "vertex" in d else None
        return VertexProperty(reader.toObject(d["id"]),
                              d["label"],
                              reader.toObject(d["value"]),
                              vertex)


class PropertyDeserializer(_GraphSONTypeIO):
    graphson_type = "g:Property"

    @classmethod
    def objectify(cls, d, reader):
        element = reader.toObject(d["element"]) if "element" in d else None
        return Property(d["key"], reader.toObject(d["value"]), element)


class PathDeserializer(_GraphSONTypeIO):
    graphson_type = "g:Path"

    @classmethod
    def objectify(cls, d, reader):
        return Path(reader.toObject(d["labels"]), reader.toObject(d["objects"]))


class TDeserializer(_GraphSONTypeIO):
    graphson_type = "g:T"

    @classmethod
    def objectify(cls, d, reader):
        return T[d]


class DirectionIO(_GraphSONTypeIO):
    graphson_type = "g:Direction"
    graphson_base_type = "Direction"
    python_type = Direction

    @classmethod
    def dictify(cls, d, writer):
        return GraphSONUtil.typedValue(cls.graphson_base_type, d.name, "g")

    @classmethod
    def objectify(cls, d, reader):
        return Direction[d]


class TraversalMetricsDeserializer(_GraphSONTypeIO):
    graphson_type = "g:TraversalMetrics"

    @classmethod
    def objectify(cls, d, reader):
        return reader.toObject(d)


class MetricsDeserializer(_GraphSONTypeIO):
    graphson_type = "g:Metrics"

    @classmethod
    def objectify(cls, d, reader):
        return reader.toObject(d)
