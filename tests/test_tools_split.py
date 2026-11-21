"""app.lol.tools re-exports the helpers that moved to formatting / payloads / scoring /
champ_select, so `from app.lol.tools import X` and `tools.X` keep working.

Each re-exported name must be the very object defined in its new module (not a copy or a
wrapper), and the new modules must never import app.lol.tools (tools imports them, so that
would be a cycle).
"""
import ast
import importlib
import inspect
import unittest

from app.lol import tools

NEW_MODULES = ("formatting", "payloads", "scoring", "champ_select")


def definedNames(module):
    """names bound at top level by a def, class or assignment in the module's own source"""
    names = []
    for node in ast.parse(inspect.getsource(module)).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
    return names


class ToolsReexportTest(unittest.TestCase):

    def test_every_moved_name_is_the_same_object_on_tools(self):
        for name in NEW_MODULES:
            module = importlib.import_module("app.lol." + name)
            names = definedNames(module)
            self.assertTrue(names, name)
            for attr in names:
                with self.subTest(module=name, name=attr):
                    self.assertIs(getattr(tools, attr), getattr(module, attr))

    def test_moved_names_are_defined_only_once(self):
        seen = {}
        for name in ("tools",) + NEW_MODULES:
            module = importlib.import_module("app.lol." + name)
            for attr in definedNames(module):
                with self.subTest(name=attr):
                    self.assertNotIn(attr, seen, f"{attr} defined in {seen.get(attr)} and {name}")
                seen[attr] = name

    def test_new_modules_do_not_import_tools(self):
        for name in NEW_MODULES:
            module = importlib.import_module("app.lol." + name)
            for node in ast.walk(ast.parse(inspect.getsource(module))):
                if isinstance(node, ast.ImportFrom):
                    targets = [node.module or ""] + [a.name for a in node.names]
                elif isinstance(node, ast.Import):
                    targets = [a.name for a in node.names]
                else:
                    continue
                with self.subTest(module=name, line=node.lineno):
                    self.assertFalse([t for t in targets if t == "tools" or t.endswith(".tools")],
                                     ast.unparse(node))


if __name__ == "__main__":
    unittest.main()
