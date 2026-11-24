# Database Management Systems (DBMS) Project Requirements

This document contains **all rules, structural and technical requirements, topic proposal guidelines, and submission/presentation details** for the Database Management Systems (DBMS) course project.

## 📌 General Rules, Submission, and Presentation Details

* **Group Size:** You may work in a group of **up to 3 students**.
* **Competitive Evaluation & Originality:** Evaluation will be competitive. Reports that are similar to others will receive lower grades. You must propose and design an **original** database project (e.g., e-commerce, healthcare, logistics, event management) that is complex enough to satisfy all requirements below.
* **Report Contents:** Ensure your report is well-organized and includes all important **code snippets** and **screenshots of the outputs** (also included within the presentation).
* **File Format & Naming:**
  * Each group member must upload the report as a **single PDF file**.
  * Naming format: `number_name.pdf` (e.g., `b211210001_johndoe.pdf`).
  * **Important:** Do **not** include any source files or additional documents—submit **only the single PDF**.
* **Submission Platform:** Submit via **Sabis** only (do not send anything via email).
* **Presentation Dates:** **December 10, 2026**, or **December 17, 2026**.
* **Presentation Requirement:** For a report to be evaluated, it **must be presented by each group member**.

---

## 1. Structural & Domain Requirements

Your proposed topic must naturally support the following minimum complexity:

1. **At Least 8 Tables:**
   * Must represent distinct, meaningful entities and relationships (no filler tables).
2. **At Least 2 Many-to-Many (M:N) Relationships:**
   * Properly resolved using **junction (bridge) tables** with composite or surrogate primary keys.
3. **At Least 3 Distinct User Roles:**
   * Define clear access levels and responsibilities (e.g., `Admin`, `Staff/Vendor`, `Customer/Member`).
4. **At Least 1 Multi-Step ACID Transaction:**
   * A core business workflow that modifies multiple tables and must succeed or fail as a single unit (`BEGIN` / `COMMIT` / `ROLLBACK`).
   * *Example:* Placing an order (insert into `orders`, insert into `order_items`, deduct from `inventory`, and log into `payments`).

---

## 2. Design & Implementation Requirements

### A. Intentional Normalization (3NF)
* Design your schema to satisfy **Third Normal Form (3NF)** to eliminate redundancy and update anomalies.
* If you choose to **denormalize** any table or column for performance (e.g., caching an `average_rating` or `total_price`), you must **explicitly document the decision** and justify the trade-off.

### B. Realistic Data Seeding & Performance Testing
* **Seeding Script:** Populate your database with **at least a few hundred rows** of realistic data across your core tables using a seeding script.
* **`EXPLAIN ANALYZE` & Indexing:** Run `EXPLAIN ANALYZE` on your most complex queries to:
  * Evaluate execution plans.
  * Identify bottlenecks.
  * Demonstrate the impact of **indexing** (before vs. after adding indexes).

### C. Non-Trivial SQL Queries
Go beyond basic `SELECT` and simple `JOIN` statements. Your final query suite must explicitly demonstrate all **4** of the following:
1. **Window Functions:** e.g., `RANK()`, `ROW_NUMBER()`, running totals with `OVER (PARTITION BY ...)`.
2. **Common Table Expressions (CTEs):** Using `WITH` clauses for readability or multi-stage aggregation.
3. **Subqueries:** Correlated or non-correlated subqueries in `WHERE`, `FROM`, or `SELECT`.
4. **Filtered Aggregations:** Using `GROUP BY ... HAVING`.

### D. Minimal HTML Web Interface
* **Tech Stack:** Build a lightweight web frontend using **plain HTML**, connected via a simple backend such as **Python (Flask/FastAPI)**, **Node.js (Express)**, or **PHP**.
* **Strict Rule (Parameterized Raw SQL):** All database operations **must use parameterized raw SQL queries** (no ORM; prevent SQL injection).
* **Scope (3–4 Core Views):**
  1. **Role Switcher or Login:** Toggle between the 3 user roles and demonstrate Role-Based Access Control (RBAC).
  2. **CRUD Views:** Basic HTML forms and tables to Create, Read, Update, and Delete records.
  3. **Transaction Demo Form:** Interactive form to demonstrate the multi-step ACID transaction (`COMMIT` / `ROLLBACK`).
  4. **Analytics Page:** Displays the results of your non-trivial analytical SQL queries.

---

## 3. Topic Proposal Submission Checklist

Before beginning implementation, submit a **1-page proposal** covering these items for instructor approval:

| Section | What to Include |
| :--- | :--- |
| **Project Title & Problem Statement** | 3–5 sentences describing the real-world problem your database solves. |
| **User Roles (Min. 3)** | List each role and 2–3 core actions they perform in the system. |
| **Entity List (Min. 8 Tables)** | Draft list of your planned tables and a 1-line description of each. |
| **M:N Relationships (Min. 2)** | Explicitly identify which entities have M:N relationships and name their junction tables. |
| **Multi-Step Transaction** | Describe the exact multi-table workflow that requires `COMMIT`/`ROLLBACK` protection. |
| **Planned Analytical Queries** | Outline 3–4 complex questions your database will answer (mapping to CTEs, window functions, subqueries, `HAVING`, etc.). |

---

## ✅ Project Completion Checklist

### Phase 1: Topic Proposal
- [ ] Original domain and project title chosen (3–5 sentence problem statement).
- [ ] At least 3 user roles defined with 2–3 core actions each.
- [ ] At least 8 meaningful tables drafted with 1-line descriptions.
- [ ] At least 2 M:N relationships identified with junction table names.
- [ ] Multi-step ACID transaction workflow specified.
- [ ] 3–4 analytical questions mapped to advanced SQL techniques.
- [ ] 1-page proposal submitted for instructor approval.

### Phase 2: Database Design & Implementation
- [ ] Schema normalized to 3NF (any intentional denormalization documented and justified).
- [ ] At least 8 tables and 2 junction tables (with composite or surrogate PKs) implemented.
- [ ] Seeding script written and executed to populate core tables with several hundred realistic rows.
- [ ] Multi-step ACID transaction (`BEGIN` / `COMMIT` / `ROLLBACK`) implemented and tested for both success and rollback scenarios.
- [ ] Non-trivial SQL queries written covering Window Functions, CTEs, Subqueries, and `GROUP BY ... HAVING`.
- [ ] `EXPLAIN ANALYZE` executed on complex queries; indexes added and performance gains documented.

### Phase 3: Minimal Web Interface
- [ ] Plain HTML frontend connected to a lightweight backend (Flask/FastAPI, Node/Express, or PHP).
- [ ] All queries implemented as **parameterized raw SQL**.
- [ ] Role Switcher / Login demonstrating RBAC across 3 roles.
- [ ] Basic CRUD forms and tables working.
- [ ] Transaction Demo form working.
- [ ] Analytics Page displaying complex query outputs.

### Phase 4: Report, Submission & Presentation
- [ ] Well-organized report containing all key code snippets and output screenshots.
- [ ] Exported as a single PDF named `number_name.pdf` (no extra source files).
- [ ] Uploaded individually by every group member via **Sabis**.
- [ ] Presentation prepared with participation of all group members (Dec 10 or Dec 17, 2026).