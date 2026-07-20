default:
    echo "Hello, World!"

export:
    uv run python vf_export.py --url https://kissenacycling.vanillacommunity.com/ --token-file ./token.txt --output vanilla.db --trace-api --rate-limit 10

view db="vanilla.db" port="5001":
    uv run python vf_viewer.py --db {{db}} --port {{port}}

sync:
    rsync -avcz --exclude-from rsync-exclude.txt . ec2-user@13.223.80.160:vex
