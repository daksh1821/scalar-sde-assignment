import requests
import json
import time
import os
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm



# The base URL for Apache's public Jira API
BASE_URL = "https://issues.apache.org/jira/rest/api/2"

PROJECTS = ["SPARK", "KAFKA", "HADOOP"] 

MAX_RESULTS = 100 

OUTPUT_FILE = "jira_corpus.jsonl"

STATE_FILE = "state.json"


def create_session_with_retries():
    """
    Creates a requests.Session with built-in retry logic for 429/5xx errors.
    """
    session = requests.Session()
    
    retry_strategy = Retry(
        total=5,  
        status_forcelist=[429, 500, 502, 503, 504],  
        backoff_factor=1  
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    
    return session

def transform_issue_for_llm(issue):
    """
    Converts a single raw Jira issue JSON object into multiple JSONL
    formats suitable for LLM training.
    """
    
    try:
        fields = issue.get("fields", {})
        if not fields: 
            return []
            
        issue_key = issue.get("key")

        title = fields.get("summary")
        description = fields.get("description")
        
        status_data = fields.get("status")
        status = status_data.get("name") if status_data else "Unknown"

        priority_data = fields.get("priority")
        priority = priority_data.get("name") if priority_data else "Unknown"

        reporter_data = fields.get("reporter")
        reporter = reporter_data.get("displayName") if reporter_data else "Unknown"
        
        project_data = fields.get("project")
        project = project_data.get("key") if project_data else "Unknown"

        created_at = fields.get("created")
        updated_at = fields.get("updated")
        labels = fields.get("labels", [])

        description_text = description if description else "No description provided."
        title_text = title if title else "No title provided."

        comments = []
        comment_data = fields.get("comment")
        if comment_data and comment_data.get("comments"):
            for comment in comment_data["comments"]:
                
                if comment and comment.get("body"):
                    comments.append(comment.get("body"))
        
        training_examples = []

        # Summarization Task
        full_text = description_text + "\n\n" + "\n\n".join(comments)
        training_examples.append({
            "id": f"{issue_key}_summarize",
            "project": project,
            "derived_task": "summarization",
            "instruction": "Summarize the following bug report and all its comments into a one-line title.",
            "input": full_text,
            "output": title_text
        })

        # Classification Task (Priority)
        training_examples.append({
            "id": f"{issue_key}_classify_priority",
            "project": project,
            "derived_task": "classification",
            "instruction": "Classify the priority of the following issue. Respond with only the priority level.",
            "input": f"Title: {title_text}\n\nDescription: {description_text}",
            "output": priority 
        })

        # Classification Task (Status)
        training_examples.append({
            "id": f"{issue_key}_classify_status",
            "project": project,
            "derived_task": "classification",
            "instruction": "What is the current status of this issue?",
            "input": f"Title: {title_text}\n\nDescription: {description_text}",
            "output": status 
        })
        
        # Q&A Task
        training_examples.append({
            "id": f"{issue_key}_qa_reporter",
            "project": project,
            "derived_task": "question_answering",
            "instruction": f"Who reported the issue {issue_key}?",
            "input": "", 
            "output": reporter
        })

        return training_examples

    except Exception as e:
        # We still keep the outer try/except as a final safety net
        print(f"Warning: Failed to process issue {issue.get('key')}. Error: {e}")
        return []

def load_state():
    """Loads the last successful state from state.json"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"current_project_index": 0, "current_start_at": 0}

def save_state(project_index, start_at):
    """Saves the current state to state.json"""
    state = {
        "current_project_index": project_index,
        "current_start_at": start_at
    }
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def main():
    print("Starting Jira Scraper...")
    session = create_session_with_retries()
    state = load_state()
    
    start_project_index = state["current_project_index"]
    
    with open(OUTPUT_FILE, 'a') as f:
        
        for i in range(start_project_index, len(PROJECTS)):
            project_key = PROJECTS[i]
            print(f"\n--- Starting Project: {project_key} ---")
            
            start_at = state["current_start_at"] if i == start_project_index else 0
            total_issues = -1
            
            pbar = None

            while True:
                jql = f"project = {project_key} ORDER BY created ASC"
                params = {
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": MAX_RESULTS,
                    "fields": "*all" 
                }
                
                try:
                    response = session.get(f"{BASE_URL}/search", params=params, timeout=30)
                    response.raise_for_status()  
                    
                    data = response.json()
                    
                    if total_issues == -1:
                        total_issues = data.get("total", 0)
                        print(f"Found {total_issues} total issues for {project_key}.")
                        pbar = tqdm(total=total_issues, desc=f"Scraping {project_key}", unit="issue")
                        pbar.update(start_at)
                    
                    issues = data.get("issues", [])
                    if not issues:
                        print("No more issues found. Moving to next project.")
                        break 

                    for issue in issues:
                        try:
                            llm_examples = transform_issue_for_llm(issue)
                            for example in llm_examples:
                                f.write(json.dumps(example) + "\n")
                        except Exception as e:
                            print(f"Error processing issue {issue.get('key')}: {e}. Skipping.")

                    num_fetched = len(issues)
                    start_at += num_fetched
                    if pbar: pbar.update(num_fetched)
                    
                    save_state(i, start_at)
                    
                    if start_at >= total_issues:
                        print("Completed all issues for this project.")
                        break
                        
                except requests.exceptions.HTTPError as e:
                    print(f"HTTP Error: {e.response.status_code} {e.response.text}")
                    print("Retrying after backoff...")
                except requests.exceptions.RequestException as e:
                    print(f"A network error occurred: {e}. Retrying...")
                except Exception as e:
                    print(f"An unexpected error occurred: {e}. Attempting to continue.")
                    time.sleep(30) 

            if pbar: pbar.close()
            save_state(i + 1, 0) 
            
    print("\nScraping complete. All data saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()