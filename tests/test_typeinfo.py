from __future__ import annotations

import types
import unittest

import pythoncom

from maxsurf_mcp import typeinfo


def _type_attr(name: str, typekind: int, *, funcs: int = 0, vars: int = 0):
    return types.SimpleNamespace(
        iid=f"{{{name}}}",
        typekind=typekind,
        wTypeFlags=0,
        cFuncs=funcs,
        cVars=vars,
        cImplTypes=0,
    )


class _ReferencedTypeInfo:
    def __init__(self, name: str, typekind: int, members=()):
        self.name = name
        self.members = list(members)
        self.attr = _type_attr(name, typekind, vars=len(self.members))

    def GetTypeAttr(self):
        return self.attr

    def GetDocumentation(self, memid: int):
        if memid == -1:
            return (self.name, None, None, None)
        return (self.GetNames(memid)[0], None, None, None)

    def GetNames(self, memid: int):
        for member_id, name, _value in self.members:
            if member_id == memid:
                return (name,)
        return ()

    def GetVarDesc(self, index: int):
        memid, _name, value = self.members[index]
        return (memid, value, (pythoncom.VT_I4, 0, None), 0, pythoncom.VAR_CONST)


class _OwnerTypeInfo:
    def __init__(self, handle: int, referenced_type, argument_descriptor):
        self.handle = handle
        self.referenced_type = referenced_type
        self.requested_handles = []
        self.attr = _type_attr("ISurfaces", pythoncom.TKIND_DISPATCH, funcs=1)
        self.func = types.SimpleNamespace(
            memid=42,
            cParamsOpt=0,
            funckind=pythoncom.FUNC_DISPATCH,
            invkind=pythoncom.INVOKE_FUNC,
            wFuncFlags=0,
            args=[
                (pythoncom.VT_BSTR, 1, None),
                (argument_descriptor, 1, None),
                (pythoncom.VT_R8, 1, None),
            ],
            rettype=(pythoncom.VT_VOID, 0, None),
        )

    def GetTypeAttr(self):
        return self.attr

    def GetDocumentation(self, memid: int):
        if memid == -1:
            return ("ISurfaces", None, None, None)
        return ("AddQuadrilateralFromCorners", None, None, None)

    def GetFuncDesc(self, index: int):
        self.assert_index(index)
        return self.func

    def GetNames(self, memid: int):
        return ("AddQuadrilateralFromCorners", "sName", "symmetry", "dBRx")

    def GetRefTypeInfo(self, handle: int):
        self.requested_handles.append(handle)
        if handle != self.handle:
            raise AssertionError(f"unexpected hard-coded HREFTYPE: {handle}")
        return self.referenced_type

    @staticmethod
    def assert_index(index: int):
        if index != 0:
            raise AssertionError(index)


class _OleObject:
    def __init__(self, ti):
        self.ti = ti

    def GetTypeInfo(self):
        return self.ti


class _DispatchObject:
    def __init__(self, ti):
        self._oleobj_ = _OleObject(ti)


class TypeInfoReferenceTests(unittest.TestCase):
    def test_resolves_dynamic_enum_handle_and_reports_every_value(self):
        handle = 987654
        enum_ti = _ReferencedTypeInfo(
            "SurfaceSymmetryType",
            pythoncom.TKIND_ENUM,
            [
                (101, "msSSTAsymmetric", 1),
                (102, "msSSTSymmetricalHalf", 2),
                (103, "msSSTSymmetricalFull", 3),
            ],
        )
        owner = _OwnerTypeInfo(
            handle,
            enum_ti,
            (pythoncom.VT_USERDEFINED, handle),
        )

        result = typeinfo.describe(_DispatchObject(owner), inherited_depth=0)

        self.assertEqual(owner.requested_handles, [handle])
        method = result["methods"][0]
        self.assertEqual(
            method["referenced_argument_types"],
            [{
                "argument": "symmetry",
                "argument_index": 1,
                "argument_position": 2,
                "reference_handle": handle,
                "referenced_type_name": "SurfaceSymmetryType",
                "typekind": pythoncom.TKIND_ENUM,
                "typekind_name": "TKIND_ENUM",
            }],
        )

        referenced = result["referenced_types"][0]
        self.assertEqual(referenced["type_name"], "SurfaceSymmetryType")
        self.assertEqual(referenced["typekind"], pythoncom.TKIND_ENUM)
        self.assertEqual(referenced["typekind_name"], "TKIND_ENUM")
        self.assertEqual(
            [(m["name"], m["value"]) for m in referenced["enum_members"]],
            [
                ("msSSTAsymmetric", 1),
                ("msSSTSymmetricalHalf", 2),
                ("msSSTSymmetricalFull", 3),
            ],
        )
        self.assertEqual(
            referenced["used_by"][0]["argument"], "symmetry"
        )

    def test_finds_user_defined_interface_inside_pointer_descriptor(self):
        handle = 24680
        interface_ti = _ReferencedTypeInfo(
            "ISurface", pythoncom.TKIND_INTERFACE
        )
        owner = _OwnerTypeInfo(
            handle,
            interface_ti,
            (pythoncom.VT_PTR, (pythoncom.VT_USERDEFINED, handle)),
        )

        result = typeinfo.describe(_DispatchObject(owner), inherited_depth=0)

        referenced = result["referenced_types"][0]
        self.assertEqual(referenced["type_name"], "ISurface")
        self.assertEqual(referenced["typekind"], pythoncom.TKIND_INTERFACE)
        self.assertEqual(referenced["typekind_name"], "TKIND_INTERFACE")
        self.assertEqual(referenced["used_by"][0]["argument"], "symmetry")


if __name__ == "__main__":
    unittest.main()
