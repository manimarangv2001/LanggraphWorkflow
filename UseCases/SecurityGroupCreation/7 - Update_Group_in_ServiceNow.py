import requests
import sys
import json


outputs = {
    "Status": "",
    "OutputMessage": "",
    "ErrorMessage": ""
}

inputs = json.loads(sys.argv[1])

url = 'https://hexawaretechnologiesincdemo8.service-now.com/api/now/table/sys_user_group'

user = 'pankajj@hexaware.com'
pwd = 'Pankaj@123'

request_body = {
    "name": inputs.uniquegroupname
}

headers = {"Content-Type":"application/json","Accept":"application/json"}

response = requests.post(url, auth=(user, pwd), headers=headers ,data=json.dumps(request_body))

if response.status_code != 200: 
    outputs["Status"] = "Error"
    outputs["ErrorMessage"] = response.json()
else:
    outputs["Status"] = "Success"
    outputs["ErrorMessage"] = "Automation has successfully update the security group details in servicenow instance."   


result = json.dumps(outputs)

print(result)