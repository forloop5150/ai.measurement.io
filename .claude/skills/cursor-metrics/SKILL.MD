## Workflow
1. generate an HTML report that shows usage metrics in my organization for Cursor
2. Track usage for all members by month 
3. Show the metrics for each month this year
4. Put the metrics in a bar chart where each bar represents a month
5. Add a pie chart that shows a summary of requests for each model
6. Show a tabular report that shows totals requests per month for each user

## Rules
- use the ai.measurement.io repository to generate the report
- call the report Cursor-Metrics.html
- If the file does not exist create it
- If the file exists already update the data and the charts appropriately
- commit the changes when finished
- push the changes to github
- the Cursor API key is stored in the environment variable called CURSOR_API_KEY