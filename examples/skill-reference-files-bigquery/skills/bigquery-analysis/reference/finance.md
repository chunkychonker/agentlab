# Finance reference

## Contents
1. Revenue recognition
2. ARR (annual recurring revenue)
3. Billing cycles
4. Q3 revenue policy

## 1. Revenue recognition

Revenue is recognized ratably over the life of a subscription contract, not
at the moment of invoicing. A 12-month contract invoiced in full on day 1
recognizes 1/12th of the contract value each month. Usage-based line items
(e.g. metered API overage) are recognized in the month the usage occurred,
not the month it was billed.

## 2. ARR (annual recurring revenue)

`ARR = MRR * 12`, where `MRR` (monthly recurring revenue) is the sum of all
active subscription run-rates, excluding one-time fees, professional
services, and usage-based overage. A customer who upgrades mid-month
contributes their new MRR starting the day the upgrade takes effect, not
retroactively for the whole month.

## 3. Billing cycles

Standard billing cycle is monthly, billed in advance, on the anniversary of
the contract start date. Annual-plan customers are billed in advance for the
full year and receive a 15% discount versus paying monthly. Failed payments
retry on a 3-7-14 day schedule before the account is marked past-due.

## 4. Q3 revenue policy

For Q3 (Jul-Sep) close, any contract signed on or before the last business
day of the quarter counts toward that quarter's bookings even if the
invoice is generated in the following week, as long as the signed order
form is dated within the quarter.
