FROM node:20-slim
WORKDIR /usr/src/app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]
