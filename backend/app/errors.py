class AppError(Exception):
    status_code = 500


class UploadTooLargeError(AppError):
    status_code = 413


class DocumentNotFoundError(AppError):
    status_code = 404


class EntityNotFoundError(AppError):
    status_code = 404
