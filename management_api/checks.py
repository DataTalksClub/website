from django.core.checks import Error, Tags, register

from .openapi import ADMIN_API_PREFIX, generate_document
from .parity import parity_errors, runtime_operations


@register(Tags.security)
def check_management_api_contract(app_configs: object, **kwargs: object) -> list[Error]:
    del app_configs, kwargs
    problems = list(parity_errors())
    schema = generate_document()
    schema_operations = {
        (f"{ADMIN_API_PREFIX}{route}", method.upper(), operation["operationId"])
        for route, methods in schema["paths"].items()
        for method, operation in methods.items()
    }
    runtime = {
        (operation.route, operation.method, operation.operation_id)
        for operation in runtime_operations()
    }
    if schema_operations != runtime:
        problems.append("OpenAPI and runtime admin operations differ")
    serialized = str(schema)
    if "_fixtures" in serialized or "TokenAuth" in serialized:
        problems.append("OpenAPI contains a fixture or legacy authentication contract")
    return [
        Error(
            problem,
            id="management_api.E001",
            hint="Repair the neutral registry, route, service, or generated schema drift.",
        )
        for problem in problems
    ]
