FROM nginx:alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY ./public /usr/share/nginx/html

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
