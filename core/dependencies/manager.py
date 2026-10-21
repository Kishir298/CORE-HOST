from core.errors import CircularDependencyError, DependencyError


class DependencyManager:
    """Manages dependency relationships within C.O.R.E."""

    def __init__(self) -> None:
        self._dependencies: dict[str, set[str]] = {}

    def register(
        self,
        component_id: str,
        dependencies: list[str] | None = None,
    ) -> None:
        if component_id not in self._dependencies:
            self._dependencies[component_id] = set()

        self._dependencies[component_id].update(dependencies or [])

    def unregister(self, component_id: str) -> None:
        if component_id not in self._dependencies:
            raise DependencyError(
                f"Component not registered: {component_id}"
            )

        del self._dependencies[component_id]

        for dependencies in self._dependencies.values():
            dependencies.discard(component_id)

    def set_dependencies(
        self,
        component_id: str,
        dependencies: list[str],
    ) -> None:
        if component_id not in self._dependencies:
            raise DependencyError(
                f"Component not registered: {component_id}"
            )

        self._dependencies[component_id] = set(dependencies)

    def get_dependencies(self, component_id: str) -> list[str]:
        if component_id not in self._dependencies:
            raise DependencyError(
                f"Component not registered: {component_id}"
            )

        return sorted(self._dependencies[component_id])

    def get_start_order(self, component_id: str) -> list[str]:
        if component_id not in self._dependencies:
            raise DependencyError(
                f"Component not registered: {component_id}"
            )

        order: list[str] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(current: str) -> None:
            if current in visiting:
                raise CircularDependencyError(
                    f"Circular dependency detected involving: {current}"
                )

            if current in visited:
                return

            if current not in self._dependencies:
                raise DependencyError(
                    f"Missing dependency: {current}"
                )

            visiting.add(current)

            for dependency in sorted(self._dependencies[current]):
                visit(dependency)

                if dependency != component_id and dependency not in order:
                    order.append(dependency)

            visiting.remove(current)
            visited.add(current)

        visit(component_id)

        return order

    def has_dependency(
        self,
        component_id: str,
        dependency_id: str,
    ) -> bool:
        if component_id not in self._dependencies:
            raise DependencyError(
                f"Component not registered: {component_id}"
            )

        return dependency_id in self._dependencies[component_id]

    def validate(self) -> None:
        for component_id in self._dependencies:
            self.get_start_order(component_id)

    def count(self) -> int:
        return len(self._dependencies)

    def clear(self) -> None:
        self._dependencies.clear()