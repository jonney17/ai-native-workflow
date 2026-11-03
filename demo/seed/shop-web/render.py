"""Tiny fake order page used by the workflow demo."""


def render_order(order):
    lines = [f"Order {order['id']} ({order['status']})"]
    for item in order.get("items", []):
        lines.append(f"  - {item}")
    return "\n".join(lines)
