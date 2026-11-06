---
artifact: design.md
ticket: SHOP-104
run: 1
author: planner
inputs: [ticket-context.md, spec.md]
status: draft
---
Implement the feature in the existing order-history flow, starting with the API contract in `shop-api` and then the account-page action in `shop-web`. Expose the order date, order number, and total in a reusable format the UI can serialize to CSV without altering unrelated order data. The account page should trigger the download directly from the customer’s currently available order history, and the CSV should include one row per order with the required columns only.
