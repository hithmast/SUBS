import ipaddress
import sys
import csv
import argparse
from dotenv import load_dotenv
import os
from queue import Queue
from collections import Counter
import logging
from concurrent.futures import ThreadPoolExecutor
import time
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BANNER = r"""
  _____  _    _  ____   _____ 
 / ____|| |  | ||  _ \ / ____|
| (___  | |  | || |_) || (___  
 \___ \ | |  | ||  _ <  \___ \ 
 ____) || |__| || |_) | ____) |
|_____/  \____/ |____/ |_____/ 
------------------------------                                           
SUBNET Searcher
Author: Aly Emara
Version: 1.0
------------------------------
"""
def check_python_version():
    """Validate if Python version is 3.7 or higher."""
    if sys.version_info < (3,7):
        raise ValueError("Python 3.7 or higher is required")

def check_config_exists():
    """Check if .env exists."""
    if not os.path.exists('.env'):
        print("Error: config.json not found")
        sys.exit(1)

def perform_initial_checks():
    """Perform all initial checks."""
    check_python_version()
    check_config_exists()

def parse_subnets():
    """Parse subnets from environment variable."""
    subnets = os.getenv("SUBNETS", "").split(",")
    subnet_networks = {}
    for subnet in subnets:
        if not subnet.strip():
            continue  # Skip empty entries
        try:
            name, cidrs = subnet.split(":")
            cidrs = cidrs.rstrip(";")  # Remove trailing semicolon if present
            subnet_networks[name] = [ipaddress.ip_network(cidr.strip()) for cidr in cidrs.split(";")]
        except ValueError:
            logging.error(f"Invalid subnet entry '{subnet}'. Expected format 'name:cidr1;cidr2;...'")
    return subnet_networks

subnet_networks = parse_subnets()

def check_ip_in_subnet(ip, subnet_name=None):
    """Check if an IP is in the specified subnet and return the subnet name."""
    try:
        ip_obj = ipaddress.ip_address(ip)
        if subnet_name:
            subnets = subnet_networks.get(subnet_name, [])
            for subnet in subnets:
                if ip_obj in subnet:
                    return ip, subnet_name
        else:
            for name, subnets in subnet_networks.items():
                for subnet in subnets:
                    if ip_obj in subnet:
                        return ip, name
        return ip, None
    except ValueError:
        logging.error(f"Invalid IP address '{ip}'")
        return ip, None

def worker(queue, results, subnet_name):
    """Worker thread to process IPs."""
    while not queue.empty():
        ip = queue.get()
        result = check_ip_in_subnet(ip, subnet_name)
        results.append(result)
        queue.task_done()

def check_ips_in_subnets(ips, subnet_name=None):
    """Check a list of IPs against subnets."""
    queue = Queue()
    results = []
    
    for ip in ips:
        queue.put(ip)
    
    with ThreadPoolExecutor(max_workers=min(10, len(ips))) as executor:
        for _ in range(min(10, len(ips))):
            executor.submit(worker, queue, results, subnet_name)
    
    return results

def read_ips_from_file(file_name, column_name=None):
    """Read IPs from a file."""
    ips = []
    try:
        if file_name.endswith('.csv') and column_name:
            with open(file_name, 'r') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    ips.append(row[column_name].strip())
        else:
            with open(file_name, 'r') as file:
                ips = [line.strip() for line in file]
    except FileNotFoundError:
        logging.error(f"File '{file_name}' not found.")
    except KeyError:
        logging.error(f"Column '{column_name}' not found in the CSV file.")
    return ips

def analyze_results(results):
    """Analyze and print the results."""
    total_ips = len(results)
    valid_ips = sum(1 for _, is_in_subnet in results if is_in_subnet)
    invalid_ips = total_ips - valid_ips
    
    stats = Counter(is_in_subnet for _, is_in_subnet in results)
    
    logging.info(f"Total IPs checked: {total_ips}")
    logging.info(f"Valid IPs: {valid_ips}")
    logging.info(f"Invalid IPs: {invalid_ips}")
    
    for status, count in stats.items():
        logging.info(f"{status}: {count}")

def print_subnets():
    """Print the configured subnets."""
    logging.info("Configured Subnets:")
    for name, subnets in subnet_networks.items():
        logging.info(f"{name}: {', '.join(str(subnet) for subnet in subnets)}")

def send_notification(results, email):
    """Send notification email with the results."""
    msg = MIMEMultipart()
    msg['From'] = os.getenv("EMAIL_SENDER")
    msg['To'] = email
    msg['Subject'] = "Subnet Searcher Results"
    
    body = "Here are the results of your subnet search:\n\n"
    body += "\n".join([f"{ip} is in the subnets: {is_in_subnet}" for ip, is_in_subnet in results])
    
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        server = smtplib.SMTP(os.getenv("SMTP_SERVER"), int(os.getenv("SMTP_PORT")))
        server.starttls()
        server.login(os.getenv("EMAIL_SENDER"), os.getenv("EMAIL_PASSWORD"))
        text = msg.as_string()
        server.sendmail(os.getenv("EMAIL_SENDER"), email, text)
        server.quit()
        logging.info("Notification email sent successfully.")
    except Exception as e:
        logging.error(f"Failed to send notification email: {e}")

def get_ip_geolocation(ip):
    """Get geolocation information for an IP address."""
    try:
        response = requests.get(f"https://ipinfo.io/{ip}/json")
        if response.status_code == 200:
            return response.json()
        else:
            logging.error(f"Failed to get geolocation for IP {ip}")
            return {}
    except Exception as e:
        logging.error(f"Error getting geolocation for IP {ip}: {e}")
        return {}
    
def output_results(results, output_format):
    """Output results in the specified format."""
    if output_format == 'json':
        with open('results.json', 'w') as file:
            json.dump(results, file, indent=4)
        logging.info("Results saved to results.json")
    elif output_format == 'csv':
        with open('results.csv', 'w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['IP', 'In Subnet'])
            writer.writerows(results)
        logging.info("Results saved to results.csv")

def send_telegram_notification(results):
    """Send notification via Telegram."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    message = "Here are the results of your subnet search:\n\n"
    message += "\n".join([f"{ip} is in the subnets: {is_in_subnet}" for ip, is_in_subnet in results])
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            logging.info("Telegram notification sent successfully.")
        else:
            logging.error(f"Failed to send Telegram notification: {response.text}")
    except Exception as e:
        logging.error(f"Error sending Telegram notification: {e}")

def main():
    """Main function to parse arguments and execute the script."""
    parser = argparse.ArgumentParser(description="Check if IPs are in specified subnets.")
    parser.add_argument('ips', nargs='*', help="List of IPs to check.")
    parser.add_argument('--file', help="File containing IPs to check.")
    parser.add_argument('--column', help="Column name in CSV file containing IPs.")
    parser.add_argument('--subnet', help="Name of the subnet to check against.")
    parser.add_argument('--psubs', action='store_true', help="Print the configured subnets.")
    parser.add_argument('--ip', help="Single IP to check.")
    parser.add_argument('--output', choices=['json', 'csv'], help="Output format for results.")
    parser.add_argument('--email', help="Email address to send the results.")
    parser.add_argument('--geolocation', action='store_true', help="Include geolocation information for IPs.")
    parser.add_argument('--telegram', action='store_true', help="Send results via Telegram.")
    args = parser.parse_args()

    if args.psubs:
        print_subnets()
        sys.exit(0)

    if args.ip:
        ips_to_check = [args.ip]
    elif args.ips:
        ips_to_check = args.ips
    elif args.file:
        ips_to_check = read_ips_from_file(args.file, args.column)
    else:
        print(BANNER)
        logging.error("No IPs provided. Use command-line arguments or specify a file.")
        sys.exit(1)

    if not ips_to_check:
        logging.error("No IPs to check. Please provide IPs via command-line arguments or a file.")
        sys.exit(1)

    start_time = time.time()
    results = check_ips_in_subnets(ips_to_check, args.subnet)
    end_time = time.time()
    
    for ip, subnet_name in results:
        if subnet_name:
            logging.info(f"{ip} is in the subnet: {subnet_name}")
        else:
            logging.info(f"{ip} is not in any configured subnet")
    
    analyze_results(results)
    
    if args.geolocation:
        for ip, _ in results:
            geo_info = get_ip_geolocation(ip)
            logging.info(f"Geolocation for {ip}: {geo_info}")
    
    if args.output:
        output_results(results, args.output)
    
    if args.email:
        send_notification(results, args.email)
    
    if args.telegram:
        send_telegram_notification(results)

    logging.info(f"Execution time: {end_time - start_time:.2f} seconds")

if __name__ == "__main__":
    print(BANNER)
    perform_initial_checks()
    main()
