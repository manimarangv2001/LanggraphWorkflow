$result = @{
    Status            = ""
    OutputMessage     = ""
    ErrorMessage      = ""
    FirstName         = ""
    LastName          = ""
    UserName          = ""
    Password          = ""
    Email             = ""
    Department        = ""
    Title             = ""
    OU                = ""
}

try {
    $response_variables = $SCTASK_RESPONSE.description

    $splitted_variable = $response_variables.split("`n")

    foreach ($variable_line in $splitted_variable) {
        $variable_key = $variable_line.split(":")[0]
        $variable_value = $variable_line.split(":")[1]

        if ($variable_key -eq "") {
            continue
        }

        if ($variable_key -eq "FirstName") {
            $result['FirstName'] = $variable_value
        }
        if ($variable_key -eq "LastName") {
            $result['LastName'] = $variable_value
        }
        if ($variable_key -eq "UserName") {
            $result['UserName'] = $variable_value
        }
        if ($variable_key -eq "Password") {
            $result['Password'] = $variable_value
        }
        if ($variable_key -eq "Email") {
            $result['Email'] = $variable_value
        }
        if ($variable_key -eq "Department") {
            $result['Department'] = $variable_value
        }
        if ($variable_key -eq "Title") {
            $result['Title'] = $variable_value
        }
        if ($variable_key -eq "OU") {
            $result['OU'] = $variable_value
        }
    }

    

    $result.Status = "Success"
    $result.OutputMessage = "Mandatory parameters are parsed successfully."
}
catch {
    $result.ErrorMessage = "ErrorCode: " + $_.Exception.Message
    $result.Status = "Error"
}

$result = $result | ConvertTo-Json
$result
