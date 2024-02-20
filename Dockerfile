# Use a lightweight base image
FROM debian:bullseye-slim

WORKDIR /usr/src/app
RUN chmod 777 /usr/src/app
# Install necessary packages and build dependencies
RUN apt-get update && \
    apt-get install -y \
    python3 \
    python3-pip \
    git \
    ffmpeg \
    build-essential \
    python3-dev \
    libffi-dev \
    bash \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file and install dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy the rest of the application files
COPY . .

# Make start.sh executable
RUN chmod +x start.sh

# Run the application
CMD ["bash", "start.sh"]
