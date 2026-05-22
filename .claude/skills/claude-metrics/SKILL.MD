## Workflow
1. generate an HTML report that shows usage metrics in my organization for Claude Code
2. Track usage for all members in my Organizations API Plan by month 
3. Show the metrics for each month this year
4. Put the metrics in a bar chart where each bar represents a month and the height of the bar represents the total number of requests for that month.
5. Add a pie chart that shows a summary of requests for each model
6. Show a tabular report that shows totals requests per month for each user. Just show the user's name, not their email in the report. 
7. Show another tabular report that shows total uaer requests for each model. Just show the user's name, not their email in the report.

## Rules
- use the ai.measurement.io repository to generate the report
- call the report Claude-Metrics.html
- If the file does not exist create it
- If the file exists already update the data and the charts appropriately
- commit the changes when finished
- push the changes to github
- the Claude API key is stored in the environment variable called ANTHROPIC_ADMIN_KEY