class BaseError(Exception):
    pass


class NetworkError(BaseError):
    def __init__(self, message="网络异常"):
        super().__init__(message)


class UnauthorizedError(BaseError):
    def __init__(self, message="登录失效，请重新登录"):
        super().__init__(message)


class DataError(BaseError):
    def __init__(self, message="数据无效"):
        super().__init__(message)


class NotFound(BaseError):
    def __init__(self, message="未找到"):
        super().__init__(message)
