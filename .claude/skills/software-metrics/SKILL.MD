## Workflow
1. generate an HTML report that shows SDLC metrics in my organization 
2. Track metrics for the following squads: SUM, DAY, STAR, VUDU, GRAD, BTC, MAX, SUBA, LEO, AURA, BACN, SQ42, PLT, WM3, ABQ and CC
3. Show the metrics for each month this year
4. Also show the metrics for this year total to date and compare to 2025
5. Track the following metrics: 
   - Working Cycle Time
   - PR cyccle time
   - Number of pull requests
   - Number of commits
   - Number of code reviews
   - Average time to merge a pull request
6. Show a the totals for all teams in one part of the report using a bar chart where each bar represents a month and the height of the bar represents the total for that metric. For example, one bar chart for working cycle time, one bar chart for PR cycle time, etc.
7. Show yearly totols in a separate section of the report with a bar chart for each metric comparing 2025 to 2026 YTD. 


## Rules
- use the ai.measurement.io repository to generate the report
- call the report SDLC-Metrics.html
- If the file does not exist create it
- If the file exists already update the data and the charts appropriately
- use the Atlassian MCP server to get the data from Jira and GitHub
- commit the changes when finished
- push the changes to github
- the Claude API key is stored in the environment variable called ANTHROPIC_ADMIN_KEY