# TimerTrigger - Python

The `TimerTrigger` makes it incredibly easy to have your functions executed on a schedule. This sample demonstrates a simple use case of calling your function every 5 minutes.

## How it works

For a `TimerTrigger` to work, you provide a schedule in the form of a [cron expression](https://en.wikipedia.org/wiki/Cron#CRON_expression)(See the link for full details). A cron expression is a string with 6 separate expressions which represent a given schedule via patterns. The pattern we use to represent every 5 minutes is `0 */5 * * * *`. This, in plain text, means: "When seconds is equal to 0, minutes is divisible by 5, for any hour, day of the month, month, day of the week, or year".

## Learn more

<TODO>
Documentation
chatgpt chat port 7071 unavailable fix
func new --name RenewSubscription --template "Timer trigger" //to create ,right now it is already created and in the _init_.py file we replaced with our code

func start --port 7072

azurite --skipApiVersionCheck :run in one terminal

cd renew_sub_rigger
and func start --port 7072
