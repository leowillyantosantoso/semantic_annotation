import os
import re
import libcellml

from config import UNITS_TTL, BASELINE_UNITS, TARGET_OPB, ENERGY_DIFFERENTIAL_OPB
from src.mathml_to_text import parse_equations, equation_defines, equation_to_text, equation_to_tree, MATHML_NS

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


# --- Energy differential (OPB_00506): voltages that drive gating -----------------------
#
# Selected by equation structure, not by component name. In a component, a voltage variable v
# is selected when:
#   1. the component has a gating ODE: d(x)/dt = ... with x dimensionless;
#   2. v is in the dependency chain of that ODE (e.g. V -> alpha_m, beta_m -> d(m)/dt,
#      or V -> m_inf, tau_m -> d(m)/dt);
#   3. v has units compatible with ENERGY_DIFFERENTIAL_OPB, and v is connected to a variable
#      defined by an ODE (the membrane potential). This drops voltage-unit constants such as
#      half-activation voltages and slope factors (E0_m, delta_m, shift_h);
#   4. the gating variable feeds a charge-flow variable (TARGET_OPB, e.g. m -> i_Na) through
#      algebraic equations and connections. This drops gates of non-electrical fluxes, such as
#      SR Ca2+ release (calcium_dynamics.g in ten Tusscher 2004, calcium_release in NVKN 1998).
# Each component's copy is kept separately: in Hodgkin-Huxley this gives sodium_channel_m_gate.V,
# sodium_channel_h_gate.V and potassium_channel_n_gate.V.

def _ci_names(element):
    return {(ci.text or "").strip() for ci in element.iter(f"{{{MATHML_NS}}}ci")}


def _equation_parts(equation):
    """(defined variable, is_ode, variables used on the right-hand side), or None."""
    lhs, rhs = equation[1], equation[2]
    if lhs.tag == f"{{{MATHML_NS}}}ci":
        return (lhs.text or "").strip(), False, _ci_names(rhs)
    if lhs.tag == f"{{{MATHML_NS}}}apply" and len(lhs) and lhs[0].tag == f"{{{MATHML_NS}}}diff":
        targets = [c for c in lhs[1:] if c.tag == f"{{{MATHML_NS}}}ci"]
        if targets:
            bvars = set().union(*(_ci_names(b) for b in lhs if b.tag == f"{{{MATHML_NS}}}bvar"))
            return (targets[0].text or "").strip(), True, _ci_names(rhs) - bvars
    return None


def _component_equations(comp):
    parts = (_equation_parts(eq) for eq in parse_equations(comp.math()) if len(eq) == 3)
    return [p for p in parts if p is not None]


def _is_dimensionless(units):
    dimensionless = libcellml.Units("dimensionless_check")
    dimensionless.addUnit("dimensionless")
    return units is not None and libcellml.Units.compatible(units, dimensionless)


def _linked_to_ode(variable, ode_states):
    """True if `variable`, or any variable connected to it, is a state defined by an ODE."""
    visited = set()
    queue = [variable]
    while queue:
        var = queue.pop()
        key = (var.parent().name(), var.name())
        if key in visited:
            continue
        visited.add(key)
        if key in ode_states:
            return True
        queue.extend(var.equivalentVariable(i) for i in range(var.equivalentVariableCount()))
    return False


def _charge_flows_fed(variable, algebraic_users, charge_targets):
    """Charge-flow variables ("component.variable") that `variable` reaches through algebraic
    equations (not ODEs) and connections. The search stops at the first charge flow on each path,
    so i_Na is returned for m but not the total current that i_Na feeds."""
    found = set()
    visited = set()
    queue = [variable]
    while queue:
        var = queue.pop()
        key = (var.parent().name(), var.name())
        if key in visited:
            continue
        visited.add(key)
        if var is not variable and var.units() is not None and _compatible_unit(var.units(), charge_targets):
            found.add(var)
            continue
        queue.extend(var.parent().variable(name) for name in algebraic_users.get(key, ()))
        queue.extend(var.equivalentVariable(i) for i in range(var.equivalentVariableCount()))
    # Report each current where it is defined, not every connected copy
    return sorted({f"{(_find_formula(v)[1] or v.parent().name())}.{v.name()}" for v in found})


def read_energy_differentials(cellml_file, opb=ENERGY_DIFFERENTIAL_OPB):
    """Voltage variables that drive a gating ODE, in the same format as read_cellml() plus:
        drives          - the gating variables (dimensionless ODE states) it drives, e.g. ["m"]
        gate_variables  - those gating variables and the rate variables defined on the way,
                          e.g. ["alpha_m", "beta_m", "m"]; used to find gate-specific evidence
        controls        - the charge-flow variables the gates feed, e.g. ["sodium_channel.i_Na"]
    """
    if not os.path.isfile(cellml_file):
        raise FileNotFoundError(f"{cellml_file} not found")

    model, _ = _parse_model(cellml_file)
    _baseline, targets = load_target_units(opb=opb)  # keep the baseline models alive
    _charge_baseline, charge_targets = load_target_units(opb=TARGET_OPB)

    components = []
    stack = [model.component(i) for i in range(model.componentCount())]
    while stack:
        comp = stack.pop(0)
        components.append(comp)
        stack.extend(comp.component(k) for k in range(comp.componentCount()))

    equations = {comp.name(): _component_equations(comp) for comp in components}
    ode_states = {(name, defined) for name, eqs in equations.items() for defined, is_ode, _ in eqs if is_ode}
    algebraic_users = {}  # (component, variable) -> variables defined algebraically from it
    for name, eqs in equations.items():
        for defined, is_ode, used in eqs:
            if not is_ode:
                for u in used:
                    algebraic_users.setdefault((name, u), set()).add(defined)

    variables = []
    for comp in components:
        dependencies = {}
        for defined, _, used in equations[comp.name()]:
            dependencies.setdefault(defined, set()).update(used)

        drives = {}  # voltage variable name -> gating variables it drives
        gate_info = {}  # gating variable -> (rate variables defined in the component, currents fed)
        for defined, is_ode, _ in equations[comp.name()]:
            gate = comp.variable(defined)
            if not is_ode or gate is None or not _is_dimensionless(gate.units()):
                continue
            controls = _charge_flows_fed(gate, algebraic_users, charge_targets)
            if not controls:
                continue
            seen, queue = set(), [defined]
            while queue:
                for name in dependencies.get(queue.pop(), ()):
                    if name not in seen:
                        seen.add(name)
                        queue.append(name)
            gate_info[defined] = ({n for n in seen if n in dependencies} | {defined}, controls)
            for name in seen:
                drives.setdefault(name, set()).add(defined)

        for name in sorted(drives):
            variable = comp.variable(name)
            units = variable.units() if variable is not None else None
            matched = _compatible_unit(units, targets) if units is not None and units.name() else None
            if matched and _linked_to_ode(variable, ode_states):
                formula, defined_in = _find_formula(variable)
                variables.append({
                    "component": comp.name(),
                    "variable": name,
                    "unit": units.name(),
                    "compatible_with": matched,
                    "formula": formula,
                    "formula_component": defined_in,
                    "drives": sorted(drives[name]),
                    "gate_variables": sorted(set().union(*(gate_info[g][0] for g in drives[name])) - {name}),
                    "controls": sorted(set().union(*(gate_info[g][1] for g in drives[name]))),
                })

    return variables
