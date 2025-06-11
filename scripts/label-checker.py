#!/usr/bin/env python3
import os
import subprocess
import re
from github import Github
import requests

token = os.environ["GITHUB_TOKEN"]
repo_name = os.environ["REPO_NAME"]
pr_number = int(os.environ["PR_NUMBER"])
branch = os.environ["BRANCH_NAME"]
pr_title = os.environ["PR_TITLE"]

EXPECTED_LABELS = set()

def get_changed_files(base_branch):
    result = subprocess.run(
        ["git", "diff", "--name-status", f"origin/{base_branch}...HEAD"],
        capture_output=True, text=True
    )
    files = []
    for line in result.stdout.strip().splitlines():
        if "\t" in line:
            status, path = line.split('\t', 1)
            files.append((status, path))
    return files

def check_label_conditions(files):
    has_impex = False
    has_items = False
    has_cache = False

    if branch.lower().startswith("conflict"):
        EXPECTED_LABELS.add("conflict")

    for status, file in files:
        if status == "D":
            continue

        if file.endswith(".impex"):
            has_impex = True
        if file.endswith("-items.xml"):
            has_items = True
        if file.endswith(".java"):
            try:
                with open(file, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    if re.search(r"@cacheable|@cacheble", content, re.IGNORECASE):
                        has_cache = True
            except:
                continue

    if has_impex:
        EXPECTED_LABELS.add("IMPEX")
    if has_items:
        EXPECTED_LABELS.add("ITEMS")
    if has_cache:
        EXPECTED_LABELS.add("CACHE")

def extract_issue_keys(base_branch):
    issue_keys = set()
    issue_keys.update(re.findall(r"[A-Z]{2,4}-\d+", branch))
    issue_keys.update(re.findall(r"[A-Z]{2,4}-\d+", pr_title))

    result = subprocess.run(
        ["git", "log", f"origin/{base_branch}..HEAD", "--pretty=format:%s"],
        capture_output=True, text=True
    )
    for msg in result.stdout.strip().splitlines():
        issue_keys.update(re.findall(r"[A-Z]{2,4}-\d+", msg))

    EXPECTED_LABELS.update(issue_keys)

def set_milestone_via_api(milestone_number):
    """GitHub REST API kullanarak milestone set et"""
    try:
        print("🔄 Setting milestone via GitHub REST API...")
        headers = {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3+json',
            'Content-Type': 'application/json'
        }
        url = f"https://api.github.com/repos/{repo_name}/issues/{pr_number}"
        data = {'milestone': milestone_number}
        
        response = requests.patch(url, json=data, headers=headers)
        
        if response.status_code == 200:
            print("✅ Milestone set successfully via REST API")
            return True
        else:
            print(f"❌ REST API failed: {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ REST API exception: {e}")
        return False

def set_milestone(pr, repo):
    base_branch = pr.base.ref
    milestone_name = None

    if base_branch.startswith("development"):
        milestone_name = "sprint-dev"
    elif base_branch.startswith("feature/marketplace"):
        milestone_name = "marketplace"
    elif base_branch.startswith("release/upgrade"):
        milestone_name = "cloud"
    elif base_branch.startswith("offline_kasa"):
        milestone_name = "offline-kasa"

    if not milestone_name:
        print("ℹ️ No milestone pattern matched for this branch")
        return

    print(f"🎯 Target milestone: {milestone_name}")

    # Mevcut milestone'ları al
    milestones = list(repo.get_milestones(state="all"))
    target_milestone = None

    for milestone in milestones:
        if milestone.title == milestone_name:
            target_milestone = milestone
            break

    # Milestone yoksa oluştur
    if not target_milestone:
        try:
            print(f"🆕 Creating new milestone: {milestone_name}")
            target_milestone = repo.create_milestone(title=milestone_name)
        except Exception as e:
            print(f"❌ Failed to create milestone {milestone_name}: {e}")
            return

    # Debug bilgileri
    print(f"🔍 Found milestone: {target_milestone.title} (ID: {target_milestone.number})")
    
    # Mevcut milestone kontrolü
    if pr.milestone and pr.milestone.title == milestone_name:
        print(f"ℹ️ Milestone {milestone_name} already set")
        return
    
    print(f"📌 Setting milestone: {milestone_name}")
    
    # REST API ile milestone set et
    success = set_milestone_via_api(target_milestone.number)
    
    if not success:
        print("❌ All milestone setting methods failed")

def sync_labels(pr, repo):
    current_labels = {label.name for label in pr.get_labels()}
    repo_labels = {label.name for label in repo.get_labels()}

    # Issue label'ları oluştur
    for label in EXPECTED_LABELS:
        if re.match(r"[A-Z]{2,4}-\d+", label):
            try:
                if label not in repo_labels:
                    print(f"🏷️ Creating missing issue label: {label}")
                    repo.create_label(name=label, color="ededed")
            except Exception as e:
                if "already_exists" in str(e):
                    print(f"ℹ️ Label {label} already exists.")
                else:
                    print(f"❌ Failed to create label {label}: {e}")

    # Repo label'larını yeniden al
    repo_labels = {label.name for label in repo.get_labels()}

    # Eklenecek label'lar
    to_add = EXPECTED_LABELS - current_labels
    to_add = [label for label in to_add if label in repo_labels]

    print("📌 Will add labels:", list(to_add))
    for label in to_add:
        try:
            print(f"➕ Adding label: {label}")
            pr.add_to_labels(label)
        except Exception as e:
            print(f"❌ Failed to add label {label}: {e}")

    # Kaldırılacak sistem label'ları
    system_labels = {"IMPEX", "CACHE", "ITEMS", "conflict"}
    to_remove = current_labels.intersection(system_labels) - EXPECTED_LABELS
    for label in to_remove:
        try:
            print(f"➖ Removing label: {label}")
            pr.remove_from_labels(label)
        except Exception as e:
            print(f"⚠️ Failed to remove label {label}: {e}")

def main():
    print("🚀 Starting label checker script...")
    
    g = Github(token)
    repo = g.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    base_branch = pr.base.ref

    print(f"📌 PR#{pr_number} is targeting base branch: {base_branch}")

    files = get_changed_files(base_branch)
    print(f"📁 Found {len(files)} changed files")
    
    check_label_conditions(files)
    extract_issue_keys(base_branch)
    
    print(f"🏷️ Expected labels: {list(EXPECTED_LABELS)}")
    
    sync_labels(pr, repo)
    set_milestone(pr, repo)
    
    print("✅ Script completed successfully")

if __name__ == "__main__":
    main()
