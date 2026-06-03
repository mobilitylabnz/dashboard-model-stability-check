$dir = "C:\Users\IvanVelilla\Documents\Projects\Western LX"

# Kill any streamlit instances already running so we get port 8501
Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {
    (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine -like "*streamlit*"
} | Stop-Process -Force

Set-Location $dir
& "$dir\.venv\Scripts\python.exe" -m streamlit run "$dir\dashboard.py" --server.headless=true
