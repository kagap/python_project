@echo off
rem usage: pbi data.csv [more pbigen options]   -> builds a Power BI project next to the file and opens it in Power BI Desktop
python "%~dp0pbigen.py" %* --open --out "%~dp0out"
