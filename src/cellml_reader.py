import os
import libcellml

ALLOWED_UNITS = {"microA_per_cm2", "uA_per_mm2", "uA_per_mmsq", "nanoA"}

def read_cellml(cellml_file):
    variables = []

    if not os.path.isfile(cellml_file):
        raise FileNotFoundError(f"{cellml_file} not found")

    with open(cellml_file, "r") as f:
        cellml_content = f.read()

    parser = libcellml.Parser()
    parser.setStrict(False)

    model = parser.parseModel(cellml_content)

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
            unit_name = variable.units().name() if variable.units() else "N/A"

            if unit_name in ALLOWED_UNITS:
                if not any(v["component"] == comp.name() and v["variable"] == variable.name() for v in variables):
                    variables.append({
                        "component": comp.name(),
                        "variable": variable.name(),
                        "unit": unit_name
                    })

        for k in range(comp.componentCount()):
            extract_variables(comp.component(k))

    for i in range(model.componentCount()):
        extract_variables(model.component(i))

    return variables