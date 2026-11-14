class TypeRegistry:

    def __init__(self):
        self.type_to_index: dict[str, int] = {}
        self.index_to_type: dict[int, str] = {}
        self.count = 0

    def __len__(self) -> int:
        return self.count

    def __contains__(self, type_name: str) -> bool:
        return type_name in self.type_to_index

    def get_index(self, type_name: str) -> int:
        return self.type_to_index[type_name]

    def get_name(self, type_index: int) -> str:
        return self.index_to_type[type_index]

    def register_type(self, type_name: str) -> int:
        if type_name in self.type_to_index:
            return self.type_to_index[type_name]

        idx = self.count
        self.type_to_index[type_name] = idx
        self.index_to_type[idx] = type_name
        self.count += 1
        return idx

type_registry = TypeRegistry()