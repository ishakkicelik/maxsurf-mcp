from __future__ import annotations

import types
import unittest

from maxsurf_mcp import native_types as nt
from maxsurf_mcp.errors import MaxsurfValidationError


class Library:
    def __init__(self, infos=()):
        self.guid = "{LIBRARY}"
        self.infos = list(infos)
        for index, info in enumerate(self.infos):
            info.library, info.index = self, index

    def GetLibAttr(self):
        return (self.guid, 0, 3, 1, 1, 8)

    def GetTypeInfoCount(self):
        return len(self.infos)

    def GetTypeInfo(self, index):
        return self.infos[index]


class Info:
    def __init__(self, name="ITest", kind=4, *, funcs=(), variables=(),
                 implementations=(), references=None, alias=None):
        self.name = name
        self.funcs = list(funcs)
        self.variables = list(variables)
        self.implementations = list(implementations)
        self.references = references or {}
        self.requested_handles = []
        self.attr = types.SimpleNamespace(
            iid=f"{{{name}}}", typekind=kind, wTypeFlags=0,
            cFuncs=len(funcs), cVars=len(variables), cImplTypes=len(implementations),
            tdescAlias=alias)
        Library([self])
        self.library.guid = f"{{{name}-LIBRARY}}"

    def GetTypeAttr(self):
        return self.attr

    def GetContainingTypeLib(self):
        return self.library, self.index

    def GetDocumentation(self, memid):
        if memid == -1:
            return (self.name, "PROPRIETARY TYPE HELP", 7, "installed.chm")
        return (self.GetNames(memid)[0], "PROPRIETARY MEMBER HELP", 8, "installed.chm")

    def GetFuncDesc(self, index):
        func = self.funcs[index]
        if isinstance(func, Exception):
            raise func
        return func

    def GetVarDesc(self, index):
        var = self.variables[index]
        if isinstance(var, Exception):
            raise var
        memid, _name, value, raw = var
        return (memid, value, (raw, 0, None), 0, 2)

    def GetNames(self, memid):
        for func in self.funcs:
            if not isinstance(func, Exception) and func.memid == memid:
                return func.names
        for var in self.variables:
            if not isinstance(var, Exception) and var[0] == memid:
                return (var[1],)
        raise KeyError(memid)

    def GetRefTypeInfo(self, handle):
        self.requested_handles.append(handle)
        return self.references[handle]

    def GetRefTypeOfImplType(self, index):
        return self.implementations[index][0]

    def GetImplTypeFlags(self, index):
        return self.implementations[index][1]


def function(name="Value", dispid=7, invkind=1, args=(), returns=24, optional=0):
    return types.SimpleNamespace(
        names=(name,) + tuple(f"value{index}" for index in range(len(args))),
        memid=dispid, invkind=invkind, args=args, rettype=(returns, 0, None),
        cParamsOpt=optional, wFuncFlags=0, funckind=4, callconv=4)


class NativeTypesTests(unittest.TestCase):
    def test_resolves_dynamic_handle_enum_values_and_alias(self):
        enum = Info("Mode", 0, variables=[(1, "One", 1, 3), (2, "Two", 2, 3)])
        alias = Info("ModeAlias", 6, alias=(29, 951))
        alias.references[951] = enum
        owner = Info(references={803: alias})
        value_type = nt.descriptor(owner, (29, 803))
        self.assertEqual(owner.requested_handles, [803])
        self.assertEqual(alias.requested_handles, [951])
        self.assertEqual(value_type["target"]["values"][1]["value"], 2)
        self.assertEqual(nt.validate_value(2, value_type), 2)
        with self.assertRaises(MaxsurfValidationError):
            nt.validate_value(True, value_type)
        with self.assertRaises(MaxsurfValidationError):
            nt.validate_value(9, value_type)

    def test_unflagged_parameter_is_a_required_native_input(self):
        # Given: native metadata uses PARAMFLAG_NONE for a collection index.
        parameter = nt.parameter(Info(), (3, 0, None), "index", 0)
        # When: named arguments are validated against that native contract.
        member = {"arguments": [parameter], "optional_count": 0}
        result = nt.validate_arguments(member, {"index": 2})
        # Then: preserve the flag and pass the input, rather than omitting it.
        self.assertEqual(parameter["flags"], 0)
        self.assertTrue(parameter["input"])
        self.assertEqual(result, {"index": 2})
        with self.assertRaises(MaxsurfValidationError):
            nt.validate_arguments(member, {})

    def test_parameter_flags_preserve_default_even_without_optional_count(self):
        result = nt.parameter(Info(), (22, 49, 100), "zoomPC", 0)
        self.assertTrue(result["input"])
        self.assertTrue(result["optional"])
        self.assertTrue(result["has_default"])
        self.assertEqual(result["default"], 100)
        self.assertFalse(result["output"])
        result = nt.parameter(Info(), ((26, 5), 2, None), "height", 1)
        self.assertTrue(result["output"])
        self.assertFalse(result["input"])
        self.assertEqual(result["type"]["element"]["vartype"], 5)

    def test_recursive_alias_preserves_identity_and_is_not_claimed_supported(self):
        alias = Info("Recursive", 6, alias=(29, 123))
        alias.references[123] = alias
        result = nt.descriptor(alias, (29, 123))
        self.assertTrue(result["target"]["recursive"])
        self.assertEqual(result["target"]["reference"]["name"], "Recursive")
        with self.assertRaises(MaxsurfValidationError):
            nt.validate_value(1, result)

    def test_unresolved_reference_keeps_precise_error(self):
        result = nt.descriptor(Info(), (29, 999))
        self.assertFalse(result["supported"])
        self.assertEqual(result["error"]["type"], "KeyError")
        self.assertEqual(result["error"]["operation"], "resolve_descriptor")

    def test_arrays_and_fixed_dimensions_validate_every_element(self):
        owner = Info()
        safe = nt.descriptor(owner, (27, 5))
        self.assertEqual(nt.validate_value([1, 2.0], safe), [1, 2.0])
        with self.assertRaises(MaxsurfValidationError):
            nt.validate_value([1, float("nan")], safe)
        fixed = nt.descriptor(owner, (28, (3, (2, 1), (2, -1))))
        self.assertEqual(fixed["bounds"], [{"count": 2, "lower_bound": 1},
                                         {"count": 2, "lower_bound": -1}])
        self.assertEqual(nt.validate_value([[1, 2], [3, 4]], fixed), [[1, 2], [3, 4]])
        for invalid in ([[1, 2]], [[1], [2]], [1, 2]):
            with self.subTest(invalid=invalid), self.assertRaises(MaxsurfValidationError):
                nt.validate_value(invalid, fixed)

    def test_vartype_array_byref_flags_and_void_pointer_boundary(self):
        flagged = nt.descriptor(Info(), 0x2000 | 3)
        self.assertTrue(flagged["supported"])
        self.assertEqual(nt.validate_value([1, 2], flagged), [1, 2])
        byref = nt.descriptor(Info(), 0x4000 | 5)
        self.assertTrue(byref["byref"])
        self.assertEqual(nt.validate_value(2.0, byref), 2.0)
        void_pointer = nt.descriptor(Info(), (26, (26, 24)))
        self.assertFalse(void_pointer["supported"])
        with self.assertRaises(MaxsurfValidationError):
            nt.validate_value(None, void_pointer)

    def test_strict_scalars_and_pointer_values(self):
        for vt, valid, invalid in [
            (3, 1, [True, 1.5, "1", 2**31]),
            (19, 0, [-1, True, 2**32]),
            (5, 2.0, [True, float("inf"), float("nan"), "2", 10**400]),
            (4, 2.0, [1e39]),
            (11, True, [0, 1, "true"]),
            (8, "hull\x00name", [1, False]),
        ]:
            shape = nt.descriptor(Info(), vt)
            self.assertEqual(nt.validate_value(valid, shape), valid)
            for value in invalid:
                with self.subTest(vt=vt, value=value), self.assertRaises(MaxsurfValidationError):
                    nt.validate_value(value, shape)
        self.assertEqual(nt.validate_value(3, nt.descriptor(Info(), (26, 3))), 3)

    def test_owned_object_validator_is_required_and_runs(self):
        shape = nt.descriptor(Info(), 9)
        with self.assertRaises(MaxsurfValidationError):
            nt.validate_value({"handle": "owned"}, shape)
        calls = []

        def owned(value, descriptor):
            calls.append((value, descriptor))
            return "verified-handle"

        self.assertEqual(nt.validate_value({"handle": "owned"}, shape,
                                          object_validator=owned), "verified-handle")
        self.assertEqual(calls[0][0], {"handle": "owned"})

    def test_argument_validation_omits_optional_and_refuses_output_input(self):
        owner = Info()
        member = {"optional_count": 0, "arguments": [
            nt.parameter(owner, (3, 1, None), "index", 0),
            nt.parameter(owner, (11, 49, False), "duplicate", 1),
            nt.parameter(owner, ((26, 5), 2, None), "height", 2),
        ]}
        self.assertEqual(nt.validate_arguments(member, [1]), {"index": 1})
        self.assertEqual(nt.validate_arguments(member, {"index": 1, "duplicate": False}),
                         {"index": 1, "duplicate": False})
        for arguments in ({}, {"index": 1, "height": 0}, [True], [1, False, 0], "bad"):
            with self.subTest(arguments=arguments), self.assertRaises(MaxsurfValidationError):
                nt.validate_arguments(member, arguments)

    def test_variant_is_finite_and_does_not_accept_arbitrary_objects(self):
        shape = nt.descriptor(Info(), 12)
        self.assertEqual(nt.validate_value([None, True, "text", 1.0], shape),
                         [None, True, "text", 1.0])
        for value in (float("nan"), object(), {"python": "object"}, 2**80):
            with self.subTest(value=value), self.assertRaises(MaxsurfValidationError):
                nt.validate_value(value, shape)


if __name__ == "__main__":
    unittest.main()
