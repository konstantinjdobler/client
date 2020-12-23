import sys
import typing as t

if t.TYPE_CHECKING:
    from wandb.sdk.wandb_artifacts import Artifact as ArtifactInCreation
    from wandb.apis.public import Artifact as DownloadedArtifact

_TYPES_STRIPPED = not (sys.version_info.major == 3 and sys.version_info.minor >= 6)
if not _TYPES_STRIPPED:
    ConvertableToType = t.Union["Type", t.Type["Type"], type, t.Any]


class TypeRegistry:
    """The TypeRegistry resolves python objects to Types as well as
    deserializes JSON dicts. Additional types can be registered via
    the .add call.
    """

    _types_by_name = None
    _types_by_class = None

    @staticmethod
    def types_by_name():
        if TypeRegistry._types_by_name is None:
            TypeRegistry._types_by_name = {}
        return TypeRegistry._types_by_name

    @staticmethod
    def types_by_class():
        if TypeRegistry._types_by_class is None:
            TypeRegistry._types_by_class = {}
        return TypeRegistry._types_by_class

    @staticmethod
    def add(wb_type: t.Type["Type"]) -> None:
        assert issubclass(wb_type, Type)
        TypeRegistry.types_by_name().update({wb_type.name: wb_type})
        TypeRegistry.types_by_class().update(
            {_type: wb_type for _type in wb_type.types}
        )

    @staticmethod
    def type_of(py_obj: t.Optional[t.Any]) -> "Type":
        class_handler = TypeRegistry.types_by_class().get(py_obj.__class__)
        _type = None
        if class_handler:
            _type = class_handler.from_obj(py_obj)
        else:
            _type = ObjectType.from_obj(py_obj)
        return _type

    @staticmethod
    def type_from_dict(
        json_dict: t.Dict[str, t.Any], artifact: t.Optional["DownloadedArtifact"] = None
    ) -> "Type":
        wb_type = json_dict.get("wb_type")
        if wb_type is None:
            TypeError("json_dict must contain `wb_type` key")
        _type = TypeRegistry.types_by_name().get(wb_type)
        if _type is None:
            TypeError("missing type handler for {}".format(wb_type))
        return _type.from_json(json_dict, artifact)

    @staticmethod
    def type_from_dtype(dtype: t.Union[ConvertableToType]) -> "Type":
        # The dtype is already an instance of Type
        if isinstance(dtype, Type):
            wbtype: Type = dtype

        # The dtype is a subclass of Type
        elif issubclass(dtype, Type):
            wbtype = dtype()

        # The dtype is a subclass of generic python type
        elif isinstance(dtype, type):
            handler = TypeRegistry.types_by_class().get(dtype)

            # and we have a registered handler
            if handler:
                wbtype = handler()

            # else, fallback to object type
            else:
                wbtype = ObjectType.from_obj(dtype)

        # The dtype is a list, then we resolve the list notation
        elif isinstance(dtype, list):
            if len(dtype) == 0:
                wbtype = ListType()
            elif len(dtype) == 1:
                wbtype = ListType(TypeRegistry.type_from_dtype(dtype[0]))

            # lists of more than 1 are treated as unions
            else:
                wbtype = UnionType([TypeRegistry.type_from_dtype(dt) for dt in dtype])

        # The dtype is a dict, then we resolve the dict notation
        elif isinstance(dtype, dict):
            wbtype = DictType(
                {key: TypeRegistry.type_from_dtype(dtype[key]) for key in dtype}
            )

        # The dtype is a concrete instance, which we will treat as a constant
        else:
            wbtype = ConstType(dtype)

        return wbtype


def _params_obj_to_json_obj(
    params_obj: t.Any, artifact: t.Optional["ArtifactInCreation"] = None,
) -> t.Any:
    """Helper method"""
    if params_obj.__class__ == dict:
        return {
            key: _params_obj_to_json_obj(params_obj[key], artifact)
            for key in params_obj
        }
    elif params_obj.__class__ in [list, set, tuple, frozenset]:
        return [_params_obj_to_json_obj(item, artifact) for item in list(params_obj)]
    elif isinstance(params_obj, Type):
        return params_obj.to_json(artifact)
    else:
        return params_obj


def _json_obj_to_params_obj(
    json_obj: t.Any, artifact: t.Optional["DownloadedArtifact"] = None
) -> t.Any:
    """Helper method"""
    if json_obj.__class__ == dict:
        if "wb_type" in json_obj:
            return TypeRegistry.type_from_dict(json_obj, artifact)
        else:
            return {
                key: _json_obj_to_params_obj(json_obj[key], artifact)
                for key in json_obj
            }
    elif json_obj.__class__ == list:
        return [_json_obj_to_params_obj(item, artifact) for item in json_obj]
    else:
        return json_obj


class Type(object):
    """This is the most generic type which all types are subclasses.
    It provides simple serialization and deserialization as well as equality checks.
    A name class-level property must be uniquely set by subclasses.
    """

    # Subclasses must override with a unique name. This is used to identify the
    # class during serializations and deserializations
    name: t.ClassVar[str] = ""

    # Subclasses may override with a list of `types` which this Type is capable
    # of being initialized. This is used by the Type Registry when calling `TypeRegistry.type_of`.
    # Some types will have an empty list - for example `Union`. There is no raw python type which
    # inherently maps to a Union and therefore the list should be empty.
    types: t.ClassVar[t.List[type]] = []

    # Contains the further specification of the Type
    _params: t.Dict[str, t.Any]

    def __init__(*args, **kwargs):
        pass

    @property
    def params(self):
        if not hasattr(self, "_params") or self._params is None:
            self._params = {}
        return {}

    def assign(self, py_obj: t.Optional[t.Any] = None) -> "Type":
        """Assign a python object to the type, returning a new type representing
        the result of the assignment.

        May to be overridden by subclasses

        Args:
            py_obj (any, optional): Any python object which the user wishes to assign to
            this type

        Returns:
            Type: an instance of a subclass of the Type class.
        """
        return self.assign_type(TypeRegistry.type_of(py_obj))

    def assign_type(self, wb_type: "Type") -> "Type":
        # Default - should be overridden
        if isinstance(wb_type, self.__class__) and self.params == wb_type.params:
            return self
        else:
            return NeverType()

    def to_json(
        self, artifact: t.Optional["ArtifactInCreation"] = None
    ) -> t.Dict[str, t.Any]:
        """Generate a jsonable dictionary serialization the type.

        If overridden by subclass, ensure that `from_json` is equivalently overridden.

        Args:
            artifact (wandb.Artifact, optional): If the serialization is being performed
            for a particular artifact, pass that artifact. Defaults to None.

        Returns:
            dict: Representation of the type
        """
        res = {
            "wb_type": self.name,
            "params": _params_obj_to_json_obj(self.params, artifact),
        }
        if res["params"] is None or res["params"] == {}:
            del res["params"]

        return res

    @classmethod
    def from_json(
        cls,
        json_dict: t.Dict[str, t.Any],
        artifact: t.Optional["DownloadedArtifact"] = None,
    ) -> "Type":
        """Construct a new instance of the type using a JSON dictionary equivalent to
        the kind output by `to_json`.

        If overridden by subclass, ensure that `to_json` is equivalently overridden.

        Returns:
            _Type: an instance of a subclass of the _Type class.
        """
        return cls(**_json_obj_to_params_obj(json_dict.get("params", {}), artifact))

    @classmethod
    def from_obj(cls, py_obj: t.Optional[t.Any] = None) -> "Type":
        return cls()

    def __repr__(self):
        return "<WBType:{} | {}>".format(self.name, self.params)

    def __eq__(self, other):
        return self is other or (
            isinstance(self, Type)
            and isinstance(other, Type)
            and self.to_json() == other.to_json()
        )


class NeverType(Type):
    """all assignments to a NeverType result in a Never Type.
    NeverType is basically the invalid case
    """

    name = "never"
    types: t.ClassVar[t.List[type]] = []

    def assign_type(self, wb_type: "Type") -> "NeverType":
        return self


class AnyType(Type):
    """all assignments to an AnyType result in the
    AnyType except None which will be NeverType
    """

    name = "any"
    types: t.ClassVar[t.List[type]] = []

    def assign_type(self, wb_type: "Type") -> t.Union["AnyType", NeverType]:
        return (
            self
            if not (isinstance(wb_type, NoneType) or isinstance(wb_type, NeverType))
            else NeverType()
        )


class UnknownType(Type):
    """all assignments to an UnknownType result in the type of the assigned object
    except none which will result in a NeverType
    """

    name = "unknown"
    types: t.ClassVar[t.List[type]] = []

    def assign_type(self, wb_type: "Type") -> "Type":
        return wb_type if not isinstance(wb_type, NoneType) else NeverType()


class NoneType(Type):
    name = "none"
    types: t.ClassVar[t.List[type]] = [None.__class__]


class StringType(Type):
    name = "text"
    types: t.ClassVar[t.List[type]] = [str]


class NumberType(Type):
    name = "number"
    types: t.ClassVar[t.List[type]] = [int, float]


class BooleanType(Type):
    name = "boolean"
    types: t.ClassVar[t.List[type]] = [bool]


class ObjectType(Type):
    """Serves as a backup type by keeping track of the python object name"""

    name = "object"
    types: t.ClassVar[t.List[type]] = []

    def __init__(self, class_name: str):
        self.params.update({"class_name": class_name})

    @classmethod
    def from_obj(cls, py_obj: t.Optional[t.Any] = None) -> "ObjectType":
        return cls(py_obj.__class__.__name__)


class ConstType(Type):
    """Represents a constant value (currently only primitives supported)
    """

    name = "const"
    types: t.ClassVar[t.List[type]] = []

    def __init__(self, val: t.Optional[t.Any] = None, is_set: t.Optional[bool] = False):
        if val.__class__ not in [str, int, float, bool, set, list, None.__class__]:
            TypeError(
                "ConstType only supports str, int, float, bool, set, list, and None types. Found {}".format(
                    val
                )
            )
        if is_set or isinstance(val, set):
            is_set = True
            assert isinstance(val, set) or isinstance(val, list)
            val = set(val)

        self.params.update({"val": val, "is_set": is_set})

    @classmethod
    def from_obj(cls, py_obj: t.Optional[t.Any] = None) -> "ConstType":
        return cls(py_obj)


def _flatten_union_types(wb_types: t.List[Type]) -> t.List[Type]:
    final_types = []
    for allowed_type in wb_types:
        if isinstance(allowed_type, UnionType):
            internal_types = _flatten_union_types(allowed_type.params["allowed_types"])
            for internal_type in internal_types:
                final_types.append(internal_type)
        else:
            final_types.append(allowed_type)
    return final_types


def _union_assigner(
    allowed_types: t.List[Type],
    obj_or_type: t.Union[Type, t.Optional[t.Any]],
    type_mode=False,
) -> t.Union[t.List[Type], NeverType]:
    resolved_types = []
    valid = False
    unknown_count = 0

    for allowed_type in allowed_types:
        if valid:
            resolved_types.append(allowed_type)
        else:
            if isinstance(allowed_type, UnknownType):
                unknown_count += 1
            else:
                if type_mode:
                    assert isinstance(obj_or_type, Type)
                    assigned_type = allowed_type.assign_type(obj_or_type)
                else:
                    assigned_type = allowed_type.assign(obj_or_type)
                if isinstance(assigned_type, NeverType):
                    resolved_types.append(allowed_type)
                else:
                    resolved_types.append(assigned_type)
                    valid = True

    if not valid:
        if unknown_count == 0:
            return NeverType()
        else:
            if type_mode:
                assert isinstance(obj_or_type, Type)
                new_type = obj_or_type
            else:
                new_type = UnknownType().assign(obj_or_type)
            if isinstance(new_type, NeverType):
                return NeverType()
            else:
                resolved_types.append(new_type)
                unknown_count -= 1

    for _ in range(unknown_count):
        resolved_types.append(UnknownType())

    resolved_types = _flatten_union_types(resolved_types)
    resolved_types.sort(key=str)
    return resolved_types


class UnionType(Type):
    """Represents an "or" of types
    """

    name = "union"
    types: t.ClassVar[t.List[type]] = []

    def __init__(
        self, allowed_types: t.Optional[t.Sequence[ConvertableToType]] = None,
    ):
        assert allowed_types is None or (allowed_types.__class__ == list)
        if allowed_types is None:
            wb_types = []
        else:
            wb_types = [TypeRegistry.type_from_dtype(dt) for dt in allowed_types]

        self.params.update({"allowed_types": wb_types})

    def assign(
        self, py_obj: t.Optional[t.Any] = None
    ) -> t.Union["UnionType", NeverType]:
        resolved_types = _union_assigner(
            self.params["allowed_types"], py_obj, type_mode=False
        )
        if isinstance(resolved_types, NeverType):
            return NeverType()
        return self.__class__(resolved_types)

    def assign_type(self, wb_type: "Type") -> t.Union["UnionType", NeverType]:
        if isinstance(wb_type, UnionType):
            assignees = wb_type.params["allowed_types"]
        else:
            assignees = [wb_type]

        resolved_types = self.params["allowed_types"]
        for assignee in assignees:
            resolved_types = _union_assigner(resolved_types, assignee, type_mode=True)
            if isinstance(resolved_types, NeverType):
                return NeverType()

        return self.__class__(resolved_types)


def OptionalType(dtype: ConvertableToType) -> UnionType:  # noqa: N802
    """Function that mimics the Type class API for constructing an "Optional Type"
    which is just a Union[wb_type, NoneType]

    Args:
        dtype (Type): type to be optional

    Returns:
        Type: Optional version of the type.
    """
    return UnionType([TypeRegistry.type_from_dtype(ConvertableToType), NoneType()])


class ListType(Type):
    """Represents a list of homogenous types
    """

    name = "list"
    types: t.ClassVar[t.List[type]] = [list, tuple, set, frozenset]

    def __init__(self, element_type: t.Optional[ConvertableToType] = None):
        if element_type is None:
            wb_type: Type = UnknownType()
        else:
            wb_type = TypeRegistry.type_from_dtype(element_type)

        self.params.update({"element_type": wb_type})

    @classmethod
    def from_obj(cls, py_obj: t.Optional[t.Any] = None) -> "ListType":
        if (
            py_obj is None
            or not (  # yes, this is a bit verbose, but the mypy typechecker likes it this way
                isinstance(py_obj, list)
                or isinstance(py_obj, tuple)
                or isinstance(py_obj, set)
                or isinstance(py_obj, frozenset)
            )
        ):
            raise TypeError("ListType.from_obj expects py_obj to by list-like")
        else:
            py_list = list(py_obj)
            elm_type = (
                UnknownType() if None not in py_list else OptionalType(UnknownType())
            )
            for item in py_list:
                _elm_type = elm_type.assign(item)
                if isinstance(_elm_type, NeverType):
                    raise TypeError(
                        "List contained incompatible types. Expected type {} found item {}".format(
                            elm_type, item
                        )
                    )

                elm_type = _elm_type

            return cls(elm_type)

    def assign_type(self, wb_type: "Type") -> t.Union["ListType", NeverType]:
        if isinstance(wb_type, ListType):
            assigned_type = self.params["element_type"].assign_type(
                wb_type.params["element_type"]
            )
            if not isinstance(assigned_type, NeverType):
                return ListType(assigned_type)

        return NeverType()

    def assign(
        self, py_obj: t.Optional[t.Any] = None
    ) -> t.Union["ListType", NeverType]:
        if (  # yes, this is a bit verbose, but the mypy typechecker likes it this way
            isinstance(py_obj, list)
            or isinstance(py_obj, tuple)
            or isinstance(py_obj, set)
            or isinstance(py_obj, frozenset)
        ):
            new_element_type = self.params["element_type"]
            for obj in list(py_obj):
                new_element_type = new_element_type.assign(obj)
                if isinstance(new_element_type, NeverType):
                    return NeverType()
            return ListType(new_element_type)

        return NeverType()


# class KeyPolicy:
#     EXACT = "E"  # require exact key match
#     SUBSET = "S"  # all known keys are optional and unknown keys are disallowed
#     UNRESTRICTED = "U"  # all known keys are optional and unknown keys are Unknown


class DictType(Type):
    """Represents a dictionary object where each key can have a type
    """

    name = "dictionary"
    types: t.ClassVar[t.List[type]] = [dict]

    def __init__(
        self, type_map: t.Optional[t.Dict[str, ConvertableToType]] = None,
    ):
        if type_map is None:
            type_map = {}
        self.params.update(
            {
                "type_map": {
                    key: TypeRegistry.type_from_dtype(type_map[key]) for key in type_map
                }
            }
        )

    @classmethod
    def from_obj(cls, py_obj: t.Optional[t.Any] = None) -> "DictType":
        if not isinstance(py_obj, dict):
            TypeError("DictType.from_obj expects a dictionary")

        assert isinstance(py_obj, dict)  # helps mypy type checker
        return cls({key: TypeRegistry.type_of(py_obj[key]) for key in py_obj})

    def assign_type(self, wb_type: "Type") -> t.Union["DictType", NeverType]:
        if (
            isinstance(wb_type, DictType)
            and self.params["type_map"].keys() == wb_type.params["type_map"].keys()
        ):
            type_map = {}
            for key in self.params["type_map"]:
                type_map[key] = self.params["type_map"][key].assign_type(
                    wb_type.params["type_map"][key]
                )
                if isinstance(type_map[key], NeverType):
                    return NeverType()
            return DictType(type_map)

        return NeverType()

    def assign(
        self, py_obj: t.Optional[t.Any] = None
    ) -> t.Union["DictType", NeverType]:
        if isinstance(py_obj, dict) and self.params["type_map"].keys() == py_obj.keys():
            type_map = {}
            for key in self.params["type_map"]:
                type_map[key] = self.params["type_map"][key].assign(py_obj[key])
                if isinstance(type_map[key], NeverType):
                    return NeverType()
            return DictType(type_map)

        return NeverType()


# Special Types
TypeRegistry.add(NeverType)
TypeRegistry.add(AnyType)
TypeRegistry.add(UnknownType)

# Types with default type mappings
TypeRegistry.add(NoneType)
TypeRegistry.add(StringType)
TypeRegistry.add(NumberType)
TypeRegistry.add(BooleanType)
TypeRegistry.add(ListType)
TypeRegistry.add(DictType)

# Types without default type mappings
TypeRegistry.add(UnionType)
TypeRegistry.add(ObjectType)
TypeRegistry.add(ConstType)

__all__ = [
    "TypeRegistry",
    "NeverType",
    "UnknownType",
    "AnyType",
    "NoneType",
    "StringType",
    "NumberType",
    "BooleanType",
    "ListType",
    "DictType",
    "UnionType",
    "ObjectType",
    "ConstType",
    "OptionalType",
    "Type",
]
