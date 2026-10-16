import requests #http requests
from urllib.parse import urljoin

from bs4 import BeautifulSoup # scraping
import smtplib # mail protocol
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import datetime

now = datetime.datetime.now()

content = '' # email content placeholder

# extraction source 

def extract_data(url):
    print('Extracting From Data Source...')
    cnt = ''
    cnt +=('<b>Data Extracted:</b>\n'+'<br>'+'-'*50+'<br>')
    response = requests.get(url, timeout=15, headers={'User-Agent': 'BoG-News-Digest/1.0'})
    response.raise_for_status()
    content = response.content
    soup = BeautifulSoup(content, 'html.parser')
    for i,tag in enumerate(soup.find_all('a', href=lambda href: href and '/news_briefs/news-brief-' in href)):
        if tag.a is None:
            anchor = tag
        else:
            anchor = tag.a
        cnt += ((str(i+1)+' :: '+ '<a href="' + urljoin(url, anchor.get('href')) + '">' + tag.text + 
                 '</a>' + "\n" + '<br>') if tag.text!='More' else '')
        # print(tag.prettify) #find_all('span' ,attrs={'class':'sitestr'}))
    return(cnt)

cnt = extract_data('https://www.bog.gov.gh/news/')
content += cnt
content += ('<br>------<br>')
content += ('<br><br>End of Feed')

# email logic

print('Composing Email...')

SERVER = 'smtp.gmail.com'
PORT = 587
FROM = ''
TO = ''
PASS = '********'

msg = MIMEMultipart()

msg['Subject'] = 'Financial News updates [Automated Email]' + ' ' + str(now.day) + '-' + str(now.month) + '-' + str(now.year)
msg['From'] = FROM
msg['To'] = TO

msg.attach(MIMEText(content, 'html'))
# fp.close()

print('Initializing Server...')

server = smtplib.SMTP(SERVER, PORT)
# server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
server.set_debuglevel(1)
server.ehlo()
server.starttls()
#server.ehlo
server.login(FROM, PASS)
server.sendmail(FROM, TO, msg.as_string())

print('Email Sent...')

server.quit()