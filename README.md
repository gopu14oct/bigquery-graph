# Eliminating Relational Data Fan-Out with BigQuery Graph Measures

This repository contains a step-by-step tutorial and Python code demonstrating how to build a **Semantic Layer** in Google Cloud BigQuery. 

By leveraging BigQuery Property Graphs and native **Measures**, data teams can achieve precise financial and resource reporting, completely eliminating the common analytical pitfall of SQL data fan-out.

## 🧠 The Concept

### The Challenge: The "Data Fan-Out" Trap
In traditional relational databases, joining tables with one-to-many relationships (e.g., one Department has many Courses) duplicates the parent row for every matching child row. If a BI tool or an AI agent runs a standard `SUM(budget)` on this joined output, the parent budget is counted multiple times, resulting in wildly inflated and inaccurate metrics.

### The Solution: BigQuery Graph Measures
BigQuery solves this natively using **Property Graphs**. By embedding the `MEASURE()` keyword directly into a node's definition, you lock the aggregation logic strictly to that node's primary key (e.g., `dept_id`). 

When querying the flattened graph using the `GRAPH_EXPAND()` function, you simply wrap your measure in the `AGG()` function. BigQuery inherently understands the graph structure and evaluates the aggregation **exactly once per key**, guaranteeing absolute mathematical precision.

## 📋 Prerequisites

Before running the tutorial, ensure you have the following:

1. **Google Cloud Account:** A GCP project with billing enabled.
2. **BigQuery API:** Enabled on your GCP project.
3. **Python Environment:** Python 3.7+ installed locally or access to Google Colab.
4. **Google Cloud SDK:** Installed and authenticated.
   ```bash
   gcloud auth application-default login
