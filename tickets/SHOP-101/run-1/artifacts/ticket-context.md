---
artifact: ticket-context.md
ticket: SHOP-101
run: 1
author: system
status: draft
---
# Ticket context

# SHOP-101: Show the tracking number on the order page

- Jira: SHOP-101
- PRD requirements: PRD-7#R2
- Affected repos (first guess): shop-api, shop-web

## Ticket description

Customers ask support where their parcel is. Show the carrier tracking number on the order page.

## Jira acceptance criteria and scenarios

| Id | Criterion |
|---|---|
| J-1 | The order API returns a tracking number once an order has shipped. |
| J-2 | The order page shows the tracking number for shipped orders. |

## Relevant PRD sections

PRD-7#R2
