# Jira Issue Scraper for LLM Training

This project contains a Python-based data pipeline designed to scrape public issue data from Apache's Jira instance, process it, and transform it into a JSONL corpus suitable for training Large Language Models (LLMs).

This system is built to be efficient, fault-tolerant, and resumable, addressing all core requirements of the SDE assignment.

## Architecture Overview

The system is designed around a simple, robust Python script (`scrape.py`) that leverages the official **Jira REST API** instead of brittle HTML scraping. This approach provides several key advantages:
* **Reliability:** The API provides structured JSON data, eliminating the need for complex and error-prone HTML parsing.
* **Efficiency:** We can fetch data in batches (up to 100 issues at a time) directly, rather than by scraping individual pages.
* **Rich Data:** The API allows us to fetch all issue metadata, including comments, changelogs, and timestamps, with a single query.

The pipeline flows as follows:
1.  **Initialize:** Load the last known state from `state.json` (if it exists). This tells the script which project and page to start from.
2.  **Fetch:** A `requests.Session` (configured with automatic retries) sends a `GET` request to the `/search` API endpoint using Jira Query Language (JQL) to retrieve a paginated list of issues for a target project.
3.  **Transform:** Each raw issue JSON is passed to a transformation function. This function extracts key metadata and reformats the data into multiple "derived task" examples (e.g., summarization, classification, Q&A).
4.  **Write:** Each transformed example is written as a new line in the `jira_corpus.jsonl` file.
5.  **Save State:** After each successful page request, the *new* state (current project index and `startAt` offset) is saved to `state.json`.
6.  **Loop:** The script continues fetching pages until all issues for all target projects are processed.

## How to Run

### 1. Setup

Clone the repository and set up the Python environment:

```bash
# Clone the repository
git clone [Your-Repo-URL]
cd [Your-Repo-Folder]

# Create a virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

The target projects can be configured at the top of `scrape.py`:
```python
# Choose any 3 projects. Use their "Key" (e.g., "SPARK", "KAFKA", "HADOOP")
PROJECTS = ["SPARK", "KAFKA", "HADOOP"] 
```

### 3. Execution

Simply run the main script:

```bash
python scrape.py
```
The script will print its progress and show a progress bar for each project.

**To resume an interrupted scrape:** Just run `python scrape.py` again. It will automatically read `state.json` and pick up exactly where it left off.

## Handling Edge Cases and Reliability

The system was designed to be fault-tolerant and handle real-world data issues.

* **Network Failures (5xx) & Rate Limits (429):**
    * **Solution:** We use a `requests.Session` with a `urllib3.Retry` strategy. This adapter will automatically catch HTTP statuses `[429, 500, 502, 503, 504]` and perform an **exponential backoff** (retrying up to 5 times) before failing. This makes the scraper resilient to temporary server-side issues or rate limits.

* **Interruption / Crash (Resumability):**
    * **Solution:** The script saves its progress to `state.json` *after every single page* of issues is fetched and written. If the script is stopped (e.g., `Ctrl+C`, crash, network loss), it can be restarted and will load this state file, skipping all previously downloaded issues. The output file `jira_corpus.jsonl` is also opened in **append mode (`'a'`)**, so it never overwrites existing data.

* **Empty or Malformed Data:**
    * **Solution:** The data transformation for each issue is wrapped in a `try...except` block. If a single issue is missing a key field (e.g., no `priority` field) or is otherwise malformed, the script will print a warning, skip *only that issue*, and continue processing the rest of the batch. This prevents one bad data point from stopping the entire pipeline.

## Data Transformation for LLMs

The raw Jira JSON is not suitable for training. The `transform_issue_for_llm` function converts each issue into several structured JSONL objects, each representing a specific "task."

A single issue (like `SPARK-1234`) is "fanned out" into multiple training examples:

1.  **Summarization:**
    * **Instruction:** "Summarize the following bug report..."
    * **Input:** The full issue description + all comments.
    * **Output:** The issue's title (`summary`).

2.  **Classification (Priority):**
    * **Instruction:** "Classify the priority of the following issue."
    * **Input:** The issue title and description.
    * **Output:** The priority level (e.g., "Major", "Blocker").

3.  **Classification (Status):**
    * **Instruction:** "What is the current status of this issue?"
    * **Input:** The issue title and description.
    * **Output:** The status (e.g., "Resolved", "In Progress").

4.  **Question Answering:**
    * **Instruction:** "Who reported the issue SPARK-1234?"
    * **Input:** (Empty)
    * **Output:** The reporter's name.

This creates a diverse, high-quality dataset for instruction-tuning an LLM.

## Future Improvements

* **Asynchronous Scraping:** For even greater speed, we could use `asyncio` and `httpx` to fetch multiple pages concurrently. This would need to be carefully balanced against the API's rate limits.
* **Better Text Cleaning:** The script currently uses the raw text. A future version could use a library like `BeautifulSoup` (even on non-HTML) or regex to strip out Jira's specific markup (e.g., `{code:...}` blocks) for a cleaner text-only corpus.
* **More Derived Tasks:** We could expand the transformation step to generate more complex tasks, such as "What was the resolution of this ticket?" by parsing the `changelog` (if `expand=changelog` is used).
* **Database Storage:** Instead of a `state.json` file, a simple `sqlite3` database could be used to track downloaded issues and state, which would be more robust at massive scale.
