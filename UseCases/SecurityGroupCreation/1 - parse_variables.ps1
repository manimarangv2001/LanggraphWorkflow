$result = @{
    Status            = ""
    OutputMessage     = ""
    ErrorMessage      = ""
    Userstobeadded    = ""
    OwnerEmail        = ""
    uniquegroupname   = ""
}   

try{

    $response_variables = $SCTASK_RESPONSE.description

    $splitted_variable = $response_variables.split("`n")

    foreach ($variable_line in $splitted_variable){

        $variable_key = $variable_line.split(":")[0].trim()
        $variable_value = $variable_line.split(":")[1].trim()

        if($variable_key -eq ""){
            continue    
        }

        if($variable_key -eq "Select Users Email"){
            $result['Userstobeadded'] = $variable_value
        }
        if($variable_key -eq "Security Group"){
            $result['uniquegroupname'] = $variable_value
        }
        if($variable_key -eq "Managed By User"){
            $result['OwnerEmail'] = $variable_value
        }
    }

    $result.Status = "Success"
    $result.OutputMessage = "Mandatory parameters are parsed successfully."
}

catch
{
    $result.ErrorMessage = "ErrorCode: "+$_.Exception.Message
    $result.Status = "Error"
}

$result = $result | ConvertTo-Json
$result

