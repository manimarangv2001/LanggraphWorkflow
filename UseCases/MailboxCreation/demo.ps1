param (
    [Parameter(Mandatory=$true)]
    [string]$input1,
    [Parameter(Mandatory=$true)]
    [string]$input2
)

$outputs = @{
    input1 = $input1
    input2 = $input2
}

$outputs | ConvertTo-Json
