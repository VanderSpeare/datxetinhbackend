from fastapi import FastAPI, HTTPException, Depends, Header, WebSocket
from pydantic import BaseModel, ValidationError
from pymongo import MongoClient
from typing import List, Optional
import jwt
import logging
import uvicorn
import json
from fastapi.responses import JSONResponse
from datetime import datetime
import sys

logging.basicConfig(level=logging.INFO)
logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
logger = logging.getLogger(__name__)

app = FastAPI()

try:
    client = MongoClient('mongodb+srv://vanderspeare:009.00@cluster0.3ido8bh.mongodb.net/busData?retryWrites=true&w=majority')
    client.server_info()
    logger.info("Kết nối MongoDB thành công")
except Exception as e:
    logger.error(f"Lỗi kết nối MongoDB: {str(e)}")
    raise

db = client['busData']
trips_collection = db['buses']
users_collection = db['users']

JWT_SECRET = "my_very_secure_secret_2025"

async def verify_token(authorization: str = Header(None)):
    if not authorization:
        logger.info("No authorization header provided")
        return None
    try:
        logger.info(f"Received authorization header: {authorization}")
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Invalid authorization header")
        token = authorization.split(" ")[1]
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        user_id = payload.get("userId")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = users_collection.find_one({"_id": user_id})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        logger.info(f"Verified user_id: {user_id}")
        return user_id
    except jwt.ExpiredSignatureError:
        logger.error("Token expired")
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        logger.error("Invalid token")
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        logger.error(f"Token verification error: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Token verification error: {str(e)}")

class SearchRequest(BaseModel):
    source: Optional[str] = None
    destination: Optional[str] = None
    date: Optional[str] = None
    passengers: Optional[int] = 1
    max_budget: Optional[float] = None
    preferred_bus_type: Optional[str] = None
    operator_type: Optional[str] = None
    max_results: Optional[int] = 5

class TripRecommendation(BaseModel):
    id: str
    source: str
    destination: str
    source_station_id: Optional[str] = None
    destination_station_id: Optional[str] = None
    source_station: str
    destination_station: str
    departure_time: str
    departure_date: Optional[str] = None
    price: float
    duration: int
    bus_type: str
    operator: str
    operator_type: str
    amenities: List[str]
    rating: float
    rank_score: float
    available_seats: int
    recommendation: str

@app.on_event("startup")
async def startup_event():
    logger.info("Server FastAPI đang khởi động...")

@app.post("/api/trips/search", response_model=List[TripRecommendation])
async def search_trips(request: SearchRequest, user_id: Optional[str] = Depends(verify_token)):
    try:
        logger.info(f"Received search request: {request.model_dump()}")

        if not any([request.source, request.destination, request.date]):
            raise HTTPException(status_code=400, detail="Vui lòng cung cấp ít nhất một trong các trường: điểm đi, điểm đến, hoặc ngày đi.")

        formatted_date = None
        if request.date:
            try:
                date_obj = datetime.strptime(request.date, "%d-%b-%Y")
                formatted_date = date_obj.strftime("%d-%b-%Y")
            except ValueError:
                raise HTTPException(status_code=400, detail="Định dạng ngày không hợp lệ. Sử dụng DD-MMM-YYYY (ví dụ: 12-May-2025).")

        mongo_query = {"$and": []}
        if request.source:
            mongo_query["$and"].append({"startingPoint": {"$regex": f"^{request.source}$", "$options": "i"}})
        if request.destination:
            mongo_query["$and"].append({"destination": {"$regex": f"^{request.destination}$", "$options": "i"}})
        if formatted_date:
            mongo_query["$and"].append({"departureDate": formatted_date})
        if request.passengers:
            mongo_query["$and"].append({"availableSeats": {"$gte": request.passengers}})
        if request.max_budget is not None:
            mongo_query["$and"].append({"price": {"$lte": request.max_budget / (request.passengers or 1)}})
        if request.preferred_bus_type:
            mongo_query["$and"].append({"busType": {"$regex": f"^{request.preferred_bus_type}$", "$options": "i"}})
        if request.operator_type:
            mongo_query["$and"].append({"operatorType": {"$regex": f"^{request.operator_type}$", "$options": "i"}})

        if not mongo_query["$and"]:
            mongo_query = {}

        logger.info(f"MongoDB query: {mongo_query}")
        trips = trips_collection.find(mongo_query).sort([
            ("rankScore", -1),
            ("price", 1)
        ]).limit(request.max_results)

        recommendations = []
        for trip in trips:
            logger.debug(f"Raw trip data: {trip}")
            recommendation = "Lựa chọn phù hợp dựa trên tiêu chí tìm kiếm."
            price = float(trip.get("price", 0))
            rating = float(trip.get("rating", 0))
            duration = int(trip.get("duration", 0))
            if price > 500000:
                recommendation = "Giá hơi cao, nhưng chất lượng được đánh giá tốt."
            if rating < 4.0:
                recommendation = "Đánh giá không cao, cân nhắc lựa chọn khác."
            if duration > 180:
                recommendation = "Thời gian di chuyển dài, có thể không phù hợp nếu bạn gấp."

            trip_recommendation = TripRecommendation(
                id=str(trip.get("id", "N/A")),
                source=str(trip.get("startingPoint", "N/A")),
                destination=str(trip.get("destination", "N/A")),
                source_station_id=str(trip.get("sourceStationId", "N/A")),
                destination_station_id=str(trip.get("destinationStationId", "N/A")),
                source_station=str(trip.get("sourceStation", trip.get("startingPoint", "N/A"))),
                destination_station=str(trip.get("destinationStation", trip.get("destination", "N/A"))),
                departure_time=str(trip.get("departureTime", "N/A")),
                departure_date=str(trip.get("departureDate", "N/A")),
                price=price,
                duration=duration,
                bus_type=str(trip.get("busType", "Standard")),
                operator=str(trip.get("operator", "N/A")),
                operator_type=str(trip.get("operatorType", "Small")),
                amenities=trip.get("amenities", []),
                rating=rating,
                rank_score=float(trip.get("rankScore", 0.0)),
                available_seats=int(trip.get("availableSeats", 30)),
                recommendation=recommendation
            )
            recommendations.append(trip_recommendation)

        logger.info(f"Found {len(recommendations)} trips in MongoDB")
        if not recommendations:
            fallback_query = {
                "$or": [
                    {"startingPoint": {"$regex": f"^{request.source}$", "$options": "i"}} if request.source else {},
                    {"destination": {"$regex": f"^{request.destination}$", "$options": "i"}} if request.destination else {},
                    {"departureDate": formatted_date} if formatted_date else {}
                ]
            }
            fallback_query["$or"] = [q for q in fallback_query["$or"] if q]
            if fallback_query["$or"]:
                logger.info(f"Fallback query: {fallback_query}")
                trips = trips_collection.find(fallback_query).sort([
                    ("rankScore", -1),
                    ("price", 1)
                ]).limit(request.max_results)
                for trip in trips:
                    logger.debug(f"Raw fallback trip data: {trip}")
                    trip_recommendation = TripRecommendation(
                        id=str(trip.get("id", "N/A")),
                        source=str(trip.get("startingPoint", "N/A")),
                        destination=str(trip.get("destination", "N/A")),
                        source_station_id=str(trip.get("sourceStationId", "N/A")),
                        destination_station_id=str(trip.get("destinationStationId", "N/A")),
                        source_station=str(trip.get("sourceStation", trip.get("startingPoint", "N/A"))),
                        destination_station=str(trip.get("destinationStation", trip.get("destination", "N/A"))),
                        departure_time=str(trip.get("departureTime", "N/A")),
                        departure_date=str(trip.get("departureDate", "N/A")),
                        price=float(trip.get("price", 0)),
                        duration=int(trip.get("duration", 0)),
                        bus_type=str(trip.get("busType", "Standard")),
                        operator=str(trip.get("operator", "N/A")),
                        operator_type=str(trip.get("operatorType", "Small")),
                        amenities=trip.get("amenities", []),
                        rating=float(trip.get("rating", 0)),
                        rank_score=float(trip.get("rankScore", 0.0)),
                        available_seats=int(trip.get("availableSeats", 30)),
                        recommendation="Kết quả thay thế dựa trên tiêu chí tối thiểu."
                    )
                    recommendations.append(trip_recommendation)

        if not recommendations:
            logger.warning(f"No trips found for request: {request.model_dump()}")
            raise HTTPException(status_code=404, detail="Không tìm thấy chuyến đi nào phù hợp.")

        logger.info(f"Returning {len(recommendations)} trip recommendations")
        # FastAPI tự động serialize dựa trên response_model
        return recommendations
    except ValidationError as e:
        logger.error(f"Validation error in search_trips: {e.errors()}")
        raise HTTPException(status_code=422, detail=e.errors())
    except HTTPException as e:
        logger.error(f"HTTPException in search_trips: {str(e)}")
        raise e
    except Exception as e:
        logger.error(f"Error in search_trips: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Lỗi server: {str(e)}")

@app.get("/api/trips/search")
async def search_trips_get():
    logger.info("Received GET request for /api/trips/search")
    return JSONResponse(status_code=405, content={"detail": "Method Not Allowed. Use POST for /api/trips/search"}, media_type="application/json; charset=utf-8")

@app.get("/api/health")
async def health_check():
    logger.info("Health check endpoint called")
    return JSONResponse(content={"status": "healthy"}, media_type="application/json; charset=utf-8")

@app.websocket("/ws/trips")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            logger.info(f"WebSocket received: {data}")
            try:
                request_data = json.loads(data)
                source = request_data.get("source", "Sài Gòn TPHCM")
                destination = request_data.get("destination", "Vũng Tàu")
                date = request_data.get("date", "12-May-2025")
            except json.JSONDecodeError:
                logger.warning("Invalid JSON received, using default query")
                source = "Sài Gòn TPHCM"
                destination = "Vũng Tàu"
                date = "12-May-2025"

            try:
                date_obj = datetime.strptime(date, "%d-%b-%Y")
                formatted_date = date_obj.strftime("%d-%b-%Y")
            except ValueError:
                logger.error(f"Invalid date format: {date}")
                await websocket.send_json({"error": "Định dạng ngày không hợp lệ. Sử dụng DD-MMM-YYYY."})
                continue

            mongo_query = {
                "$and": [
                    {"startingPoint": {"$regex": f"^{source}$", "$options": "i"}},
                    {"destination": {"$regex": f"^{destination}$", "$options": "i"}},
                    {"departureDate": formatted_date}
                ]
            }
            trips = trips_collection.find(mongo_query).limit(5)
            trip_list = []
            for trip in trips:
                logger.debug(f"Raw WebSocket trip data: {trip}")
                trip_recommendation = TripRecommendation(
                    id=str(trip.get("id", "N/A")),
                    source=str(trip.get("startingPoint", "N/A")),
                    destination=str(trip.get("destination", "N/A")),
                    source_station_id=str(trip.get("sourceStationId", "N/A")),
                    destination_station_id=str(trip.get("destinationStationId", "N/A")),
                    source_station=str(trip.get("sourceStation", trip.get("startingPoint", "N/A"))),
                    destination_station=str(trip.get("destinationStation", trip.get("destination", "N/A"))),
                    departure_time=str(trip.get("departureTime", "N/A")),
                    departure_date=str(trip.get("departureDate", "N/A")),
                    price=float(trip.get("price", 0)),
                    duration=int(trip.get("duration", 0)),
                    bus_type=str(trip.get("busType", "Standard")),
                    operator=str(trip.get("operator", "N/A")),
                    operator_type=str(trip.get("operatorType", "Small")),
                    amenities=trip.get("amenities", []),
                    rating=float(trip.get("rating", 0)),
                    rank_score=float(trip.get("rankScore", 0.0)),
                    available_seats=int(trip.get("availableSeats", 30)),
                    recommendation="Cập nhật qua WebSocket"
                )
                trip_list.append(trip_recommendation.model_dump())

            logger.info(f"WebSocket sending {len(trip_list)} trips for source: {source}, destination: {destination}")
            await websocket.send_json({"trips": trip_list})
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
        await websocket.send_json({"error": f"WebSocket error: {str(e)}"})
    finally:
        await websocket.close()
        logger.info("WebSocket connection closed")

if __name__ == "__main__":
    logger.info("Starting FastAPI server on 0.0.0.0:8000")
    logger.info("Registered endpoints: /api/health, /api/trips/search (POST), /ws/trips")
    uvicorn.run(app, host="0.0.0.0", port=8000)