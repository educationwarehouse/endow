from src.endow import Domain
from tests.dependency_one import FirstDependency
from tests.dependency_two import SecondDependency


class MainDependency(Domain):
    first: FirstDependency
    second: SecondDependency
