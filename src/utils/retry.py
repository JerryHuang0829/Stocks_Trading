"""API 重試機制"""

import logging

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# 通用 API 重試裝飾器：暫時性錯誤自動重試，最多 3 次，指數退避
api_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((
        requests.ConnectionError,
        requests.Timeout,
        requests.HTTPError,
    )),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
