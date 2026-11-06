---
artifact: design.md
ticket: SHOP-102
run: 1
author: planner
inputs: [ticket-context.md, spec.md]
status: draft
---
Implement the banner in the cart UI in `shop-web`. Use the existing cart total and the application's free-shipping threshold source to determine and display the remaining amount. Hide the banner when the cart total meets or exceeds the threshold; no API or cross-repository change is currently identified.
