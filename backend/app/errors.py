class AppError(Exception):
    status_code = 500


class UploadTooLargeError(AppError):
    status_code = 413
