from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from os import getenv
from typing import Any


logger = logging.getLogger(__name__)


class PaymentGatewayError(RuntimeError):
    pass


@dataclass(frozen=True)
class PaymentAttempt:
    provider: str
    label: str
    amount: int
    pay_url: str
    idempotency_key: str


@dataclass(frozen=True)
class PaymentCheck:
    provider: str
    label: str
    status: str
    paid: bool
    amount: Decimal | None = None

    @property
    def is_succeeded(self) -> bool:
        return self.status == "succeeded" and self.paid


def payment_provider() -> str:
    return (getenv("MAIN_PAYMENT_TYPE") or "manual").strip().lower()


def is_manual_payment_mode() -> bool:
    return payment_provider() in {"", "manual", "none", "off", "false", "0"}


async def create_payment(amount: int, description: str, metadata: dict[str, Any] | None = None, return_url: str | None = None) -> PaymentAttempt | None:
    provider = payment_provider()
    if provider in {"", "manual", "none", "off", "false", "0"}:
        return None
    if amount <= 0:
        raise PaymentGatewayError("Payment amount must be positive")
    if provider == "yoomoney":
        return await _run_gateway_call(_create_yoomoney_payment, amount, description)
    if provider == "yookassa":
        return await _run_gateway_call(_create_yookassa_payment, amount, description, metadata, return_url)
    raise PaymentGatewayError(f"Unsupported MAIN_PAYMENT_TYPE={provider!r}")


async def check_payment(provider: str, label: str) -> PaymentCheck:
    provider = provider.strip().lower()
    if provider == "yoomoney":
        return await _run_gateway_call(_check_yoomoney_payment, label)
    if provider == "yookassa":
        return await _run_gateway_call(_check_yookassa_payment, label)
    raise PaymentGatewayError(f"Unsupported payment provider={provider!r}")


async def is_payment_paid(provider: str, label: str) -> bool:
    return (await check_payment(provider, label)).is_succeeded


def _create_yoomoney_payment(amount: int, description: str) -> PaymentAttempt:
    try:
        from yoomoney import Quickpay
    except Exception as exc:  # pragma: no cover - depends on optional package import
        raise PaymentGatewayError("YooMoney package is not available") from exc

    label = str(uuid.uuid4())
    receiver = getenv("YOOMONEY_RECEIVER") or "41001227633442"
    try:
        quickpay = Quickpay(
            receiver=receiver,
            quickpay_form="shop",
            targets=description[:128],
            paymentType=getenv("YOOMONEY_PAYMENT_TYPE") or "AC",
            sum=amount,
            label=label,
        )
    except Exception as exc:
        raise PaymentGatewayError("Failed to create YooMoney payment") from exc

    pay_url = getattr(quickpay, "redirected_url", None) or getattr(quickpay, "base_url", None)
    if not pay_url:
        raise PaymentGatewayError("YooMoney did not return a payment URL")
    return PaymentAttempt(provider="yoomoney", label=label, amount=amount, pay_url=str(pay_url), idempotency_key=label)


def _check_yoomoney_payment(label: str) -> PaymentCheck:
    token = getenv("YOOMONEY_TOKEN")
    if not token:
        raise PaymentGatewayError("YOOMONEY_TOKEN is not configured")
    try:
        from yoomoney import Client

        history = Client(token=token).operation_history(label=label)
    except Exception as exc:
        raise PaymentGatewayError("Failed to check YooMoney payment") from exc
    for operation in getattr(history, "operations", []) or []:
        status = str(getattr(operation, "status", "") or "").lower()
        if status:
            return PaymentCheck(provider="yoomoney", label=label, status="succeeded" if status == "success" else status, paid=status == "success")
    return PaymentCheck(provider="yoomoney", label=label, status="pending", paid=False)


def _create_yookassa_payment(amount: int, description: str, metadata: dict[str, Any] | None = None, return_url: str | None = None) -> PaymentAttempt:
    idempotency_key = str(uuid.uuid4())
    response = _yookassa_request(
        "POST",
        "/payments",
        json_body={
            "amount": {"value": _money_value(amount), "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": _return_url(return_url),
            },
            "capture": True,
            "description": _description(description),
            "metadata": _metadata(metadata),
        },
        idempotency_key=idempotency_key,
    )

    label = str(response.get("id") or "")
    confirmation = response.get("confirmation")
    pay_url = _confirmation_url(confirmation)
    if not label or not pay_url:
        raise PaymentGatewayError("YooKassa did not return payment id or URL")
    return PaymentAttempt(provider="yookassa", label=label, amount=amount, pay_url=pay_url, idempotency_key=idempotency_key)


def _check_yookassa_payment(label: str) -> PaymentCheck:
    operation = _yookassa_request("GET", f"/payments/{label}")
    check = _yookassa_check_from_operation(label, operation)
    if check.status == "waiting_for_capture" and check.paid and _env_bool("YOOKASSA_AUTO_CAPTURE_WAITING", True):
        _yookassa_request(
            "POST",
            f"/payments/{label}/capture",
            json_body={"amount": {"value": _decimal_money_value(check.amount or Decimal("0")), "currency": "RUB"}},
            idempotency_key=str(uuid.uuid4()),
        )
        operation = _yookassa_request("GET", f"/payments/{label}")
        check = _yookassa_check_from_operation(label, operation)
    return check


async def _run_gateway_call(func, *args):
    timeout = _env_float("PAYMENT_GATEWAY_TIMEOUT", 8.0)
    try:
        return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout=timeout)
    except TimeoutError as exc:
        raise PaymentGatewayError("Payment provider request timed out") from exc


def _yookassa_request(method: str, path: str, json_body: dict[str, Any] | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
    account_id = getenv("YOOKASSA_ACCOUNT_ID")
    secret_key = getenv("YOOKASSA_SECRET_KEY")
    if not account_id or not secret_key:
        raise PaymentGatewayError("YOOKASSA_ACCOUNT_ID or YOOKASSA_SECRET_KEY is not configured")
    try:
        import requests
    except Exception as exc:  # pragma: no cover - requests is a YooKassa dependency
        raise PaymentGatewayError("Requests package is not available") from exc

    url = _yookassa_api_url() + path
    headers = {"Content-Type": "application/json"}
    if idempotency_key:
        headers["Idempotence-Key"] = idempotency_key

    attempts = max(1, _env_int("YOOKASSA_HTTP_ATTEMPTS", 1))
    retry_delay = max(0.0, _env_float("YOOKASSA_HTTP_RETRY_DELAY", 0.5))
    timeout = _env_float("YOOKASSA_HTTP_TIMEOUT", 5.0)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.request(
                method,
                url,
                auth=(account_id, secret_key),
                headers=headers,
                json=json_body,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(retry_delay)
                continue
            raise PaymentGatewayError("YooKassa request failed") from exc

        if response.status_code in {202, 429, 500, 502, 503, 504} and attempt < attempts:
            time.sleep(retry_delay)
            continue
        if response.status_code >= 400:
            raise PaymentGatewayError(f"YooKassa HTTP {response.status_code}: {_short_response_text(response)}")
        try:
            data = response.json()
        except ValueError as exc:
            raise PaymentGatewayError("YooKassa returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise PaymentGatewayError("YooKassa returned unexpected response")
        return data

    raise PaymentGatewayError("YooKassa request failed") from last_error


def _confirmation_url(confirmation: Any) -> str | None:
    if confirmation is None:
        return None
    if isinstance(confirmation, dict):
        return confirmation.get("confirmation_url") or confirmation.get("url")
    return getattr(confirmation, "confirmation_url", None) or getattr(confirmation, "url", None)


def _yookassa_check_from_operation(label: str, operation: Any) -> PaymentCheck:
    if isinstance(operation, dict):
        status = str(operation.get("status") or "").lower()
        paid = bool(operation.get("paid")) or status == "succeeded"
    else:
        status = str(getattr(operation, "status", "") or "").lower()
        paid = bool(getattr(operation, "paid", False)) or status == "succeeded"
    amount = _operation_amount(operation)
    return PaymentCheck(provider="yookassa", label=label, status=status or "unknown", paid=paid, amount=amount)


def _operation_amount(operation: Any) -> Decimal | None:
    amount = operation.get("amount") if isinstance(operation, dict) else getattr(operation, "amount", None)
    if amount is None:
        return None
    value = amount.get("value") if isinstance(amount, dict) else getattr(amount, "value", None)
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _short_response_text(response: Any) -> str:
    text = str(getattr(response, "text", "") or "")
    return " ".join(text.split())[:500]


def _money_value(amount: int) -> str:
    return _decimal_money_value(Decimal(str(amount)))


def _decimal_money_value(amount: Decimal) -> str:
    return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _description(description: str) -> str:
    value = " ".join((description or "Покупка рекламы VK").split())
    return value[:128] or "Покупка рекламы VK"


def _metadata(metadata: dict[str, Any] | None) -> dict[str, str]:
    result = {"order_id": str(uuid.uuid4()), "source": "vk_ad_bot"}
    for key, value in (metadata or {}).items():
        if value is None:
            continue
        result[str(key)[:32]] = str(value)[:512]
    return result


def _return_url(return_url: str | None = None) -> str:
    explicit = (return_url or getenv("YOOKASSA_RETURN_URL") or "").strip()
    if explicit:
        return explicit
    group_id = _active_vk_group_id()
    if group_id:
        return f"https://vk.com/write-{group_id}"
    return "https://vk.com/"


def _yookassa_api_url() -> str:
    return (getenv("YOOKASSA_API_URL") or "https://api.yookassa.ru/v3").rstrip("/")


def _active_vk_group_id() -> int | None:
    token_env = getenv("ACTIVE_VK_TOKEN_ENV", "VK_TEST_TOKEN")
    group_env = "VK_TEST_GROUP_ID" if token_env == "VK_TEST_TOKEN" else "VK_GROUP_ID"
    value = getenv(group_env) or getenv("VK_GROUP_ID") or getenv("VK_TEST_GROUP_ID")
    try:
        return abs(int(value)) if value else None
    except ValueError:
        return None


def _env_bool(name: str, default: bool = False) -> bool:
    value = getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да"}


def _env_int(name: str, default: int) -> int:
    value = getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default
