---
name: deutsch-grader
description: Grade a German essay through a running Deutsch Grader deployment, poll the teacher workflow, and summarize the AI draft score and confirmed errors. Use when the user asks to grade an essay with their own Deutsch Grader instance or to test that deployment; not for standalone German grammar tutoring.
---

# Deutsch Grader

Use the deployment's teacher API. The AI output is a draft for teacher review, never a final grade.

## Prerequisites

The user must have a running instance. Read these environment variables:

- DG_API_URL, for example http://127.0.0.1:8000
- DG_API_EMAIL
- DG_API_PASSWORD

If the URL is missing, ask for it. Do not invent a deployment or claim a result without calling the API.

## Grade an essay

Run the helper:

~~~bash
python scripts/grade.py \
  --text "Meine beste Reise war im Sommer nach Qingdao." \
  --title "Meine beste Reise" \
  --student S-001
~~~

The script logs in, lists students when --student is omitted only if there is one student, submits the text, polls until ready, and prints JSON. It uses the pseudonym or student id, never a real name.

For a file:

~~~bash
python scripts/grade.py --file essay.txt --student S-001
~~~

## Report

After the script returns:

1. State the AI draft total and the three dimensions.
2. List only effective confirmed errors with original, corrected, type, and rule.
3. Mention unresolved checklist items or a needs_manual status.
4. Tell the user a teacher must review and finalize before the result is used for a grade.

If the status is needs_manual, report run.error and the next step. Do not present a partial score as complete.

## Safety

- Use a teacher account, not an admin or data-protection account.
- Do not send real student names, ID photos, or unrelated personal data.
- Do not ask for passwords in chat; the user should set DG_API_PASSWORD.
- Do not claim the deployment is available if healthz fails.
