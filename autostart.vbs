' Auto-start Intel Station (local only): launch the server hidden, wait until it
' responds, then open the page. Pass /nobrowser to skip opening the browser.
Option Explicit

Dim sh, openPage, i
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\Code\Trae\qbsys\intel-station"

openPage = True
If WScript.Arguments.Count > 0 Then
  If LCase(WScript.Arguments(0)) = "/nobrowser" Then openPage = False
End If

If Not IsUp() Then
  sh.Run """D:\software\Python3.12.8win64\python.exe"" app.py", 0, False
  For i = 1 To 40
    WScript.Sleep 500
    If IsUp() Then Exit For
  Next
End If

If openPage And IsUp() Then
  sh.Run "http://127.0.0.1:8765"
End If

Function IsUp()
  Dim req
  IsUp = False
  On Error Resume Next
  Set req = CreateObject("MSXML2.ServerXMLHTTP.6.0")
  req.setTimeouts 1000, 1000, 1000, 1500
  req.open "GET", "http://127.0.0.1:8765/", False
  req.send
  If Err.Number = 0 Then IsUp = True
  Err.Clear
  On Error GoTo 0
End Function
