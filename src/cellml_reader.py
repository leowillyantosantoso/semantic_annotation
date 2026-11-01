import os
import re
import libcellml

from config import UNITS_TTL, BASELINE_UNITS, TARGET_OPB
from src.mathml_to_text import parse_equations, equation_defines, equation_to_text, equation_to_tree

# Matches lines such as:  ex:C_per_m2_s is_unit_of: opb:OPB_00318 .
TTL_UNIT_PATTERN = re.compile(r"ex:(\S+)\s+is_unit_of:\s+opb:(OPB_\d+)")


def _parse_model(path):
    parser = libcellml.Parser()
    parser.setStrict(False)
    with open(path, "r") as f:
        return parser.parseModel(f.read()), parser


def load_target_units(ttl_file=UNITS_TTL, baseline_file=BASELINE_UNITS, opb=TARGET_OPB):
    """Return the baseline units (libcellml Units) that the TTL maps to `opb`."""
    with open(ttl_file, "r") as f:
        names = [m.group(1) for m in TTL_UNIT_PATTERN.finditer(f.read()) if m.group(2) == opb]

    baseline, _ = _parse_model(baseline_file)
    targets = []
    for name in names:
        units = baseline.units(name)
        if units is None:
            print(f"WARNING: unit '{name}' from {os.path.basename(ttl_file)} is not defined in {os.path.basename(baseline_file)}")
        else:
            targets.append(units)
    # Keep the baseline model alive: its Units refer back to it for C_per_m2 etc.
    return baseline, targets


def _compatible_unit(units, targets):
    """Name of the first target unit that `units` is dimensionally compatible with, or None."""
    for target in targets:
        if libcellml.Units.compatible(units, target):
            return target.name()
    return None


def _find_formula(variable):
    """Find the equation defining `variable`.

    Look in the variable's own component first; if it is only an input there
    (public_interface="in"), follow its connections to the component that defines it.

    Returns:
        (formula_text, defining_component_name) or (None, None)
    """
    visited = set()
    queue = [variable]

    while queue:
        var = queue.pop(0)
        comp = var.parent()
        key = (comp.name(), var.name())
        if key in visited:
            continue
        visited.add(key)

        for equation in parse_equations(comp.math()):
            if equation_defines(equation, var.name()):
                return equation_to_text(equation), comp.name()

        for i in range(var.equivalentVariableCount()):
            queue.append(var.equivalentVariable(i))

    # No equation: the variable is a constant set through its initial_value
    for var in [variable] + [variable.equivalentVariable(i) for i in range(variable.equivalentVariableCount())]:
        if var.initialValue():
            return f"{var.name()} = {var.initialValue()}", var.parent().name()

    return None, None


def read_equations(cellml_file):
    """All equations of a CellML model as [{"component", "text", "tree"}] (tree: src/expr_tree.py)."""
    model, _ = _parse_model(cellml_file)
    equations = []

    def collect(comp):
        for equation in parse_equations(comp.math()):
            equations.append({
                "component": comp.name(),
                "text": equation_to_text(equation),
                "tree": equation_to_tree(equation),
            })
        for k in range(comp.componentCount()):
            collect(comp.component(k))

    for i in range(model.componentCount()):
        collect(model.component(i))
    return equations


def read_cellml(cellml_file):
    variables = []

    if not os.path.isfile(cellml_file):
        raise FileNotFoundError(f"{cellml_file} not found")

    model, parser = _parse_model(cellml_file)
    baseline, targets = load_target_units()

    if parser.issueCount() > 0:
        print("Issues found:")
        for i in range(parser.issueCount()):
            print("-", parser.issue(i).description())

    print("")

    def extract_variables(comp):
        if comp.name() == "membrane":
            return

        for j in range(comp.variableCount()):
            variable = comp.variable(j)
            units = variable.units()
            matched = _compatible_unit(units, targets) if units else None

            if matched:
                if not any(v["component"] == comp.name() and v["variable"] == variable.name() for v in variables):
                    formula, defined_in = _find_formula(variable)
                    variables.append({
                        "component": comp.name(),
                        "variable": variable.name(),
                        "unit": units.name(),
                        "compatible_with": matched,
                        "formula": formula,
                        "formula_component": defined_in
                    })

        for k in range(comp.componentCount()):
            extract_variables(comp.component(k))

    for i in range(model.componentCount()):
        extract_variables(model.component(i))

    return variables
