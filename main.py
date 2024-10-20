import traceback
from datetime import datetime, date
from collections import defaultdict
from typing import List

import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
import uuid
from fastapi import FastAPI, Body, Depends, File, HTTPException, UploadFile
from app.auth.auth_bearer import JWTBearer
from app.auth.auth_handler import sign_jwt, decode_jwt
from app.model import GrammarCheckerSchema, UserLoginSchema, RewriterSchema, SummarizerSchema, CvGeneratorSchema, \
    ResponseGeneratorSchema, AvataryzeSchema
import psycopg2
import json
import os
from dotenv import load_dotenv
import bcrypt
from openai import OpenAI
import time
from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import io

print("Hello World")


client = OpenAI()
OpenAI.api_key = os.getenv('OPENAI_API_KEY')

load_dotenv()

app = FastAPI()

conn_str = f"dbname={os.getenv('Db_Name')} host={os.getenv('host')} port= 5432 " \
           f"user= {os.getenv('Db_user')} password={os.getenv('Db_pass')}"

openai_api_key = os.getenv("OPENAI_API_KEY")

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

print("The Server Has Started.")


def get_current_user(token: str = Depends(JWTBearer())):
    payload = decode_jwt(token)
    return payload.get('contact_no')


@app.post("/grammar-checker", tags=["grammar-checker"])
async def grammarChecker(body: GrammarCheckerSchema, contact_no: str = Depends(get_current_user)) -> dict:
    conn = psycopg2.connect(conn_str)
    cursor = conn.cursor()

    cursor.execute(
        'INSERT INTO startrek.request_logs(body, requested_by, request_time, module) VALUES (%s, %s, %s, '
        '%s) RETURNING id',
        (body.json(), contact_no, datetime.now(), "grammar-checker"))

    conn.commit()

    request_id = cursor.fetchone()[0]

    try:
        response = {
            "message": "Successful",
            "data": {
                "result": ""
            }
        }

        new_thread = client.beta.threads.create()

        new_thread_message = client.beta.threads.messages.create(
            thread_id=new_thread.id,
            role="user",
            content=body.content,
        )

        new_run = client.beta.threads.runs.create(
            thread_id=new_thread.id,
            assistant_id='asst_Hk4K5n5tQVsUScORjFY56MNk'
        )

        while new_run.status in ["queued", "in_progress"]:
            keep_retrieving_run = client.beta.threads.runs.retrieve(
                thread_id=new_thread.id,
                run_id=new_run.id
            )

            if keep_retrieving_run.status == "completed":

                all_messages = client.beta.threads.messages.list(
                    thread_id=new_thread.id
                )

                data = {
                    "user": contact_no,
                    "content": body.content,
                    "result": all_messages.data[0].content[0].text.value,
                    "thread_id": new_thread.id,
                    "run_id": new_run.id,
                    "requested_at": all_messages.data[1].created_at,
                    "completed_at": all_messages.data[0].created_at,
                    "prompt_tokens": keep_retrieving_run.usage.prompt_tokens,
                    "completion_tokens": keep_retrieving_run.usage.completion_tokens,
                    "total_tokens": keep_retrieving_run.usage.total_tokens,
                    "thread_message_object": new_thread_message.json(),
                    "run_object": new_run.json(),
                    "raw_response": all_messages.json()
                }

                cursor.execute(
                    'INSERT INTO startrek.grammar_checker_requests("user", content, result, thread_id, run_id, '
                    'requested_at, completed_at, thread_message_object, run_object, raw_response, completion_tokens, '
                    'prompt_tokens, total_tokens ) VALUES (%s, %s, %s,'
                    '%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                    (data["user"], data["content"], data["result"], data["thread_id"], data["run_id"],
                     data["requested_at"],
                     data["completed_at"], data["thread_message_object"], data["run_object"], data["raw_response"],
                     data["completion_tokens"], data["prompt_tokens"], data["total_tokens"]))

                conn.commit()
                cursor.close()
                conn.close()

                response["data"] = {
                    "result": all_messages.data[0].content[0].text.value
                }

                await insertResponseLog(
                    {"request_id": request_id, "respond_to": data["user"], "response": json.dumps(response),
                     "response_time": datetime.now()})
                return response

            elif keep_retrieving_run.status == "queued" or keep_retrieving_run.status == "in_progress":
                pass
            else:
                break

        await insertResponseLog(
            {"request_id": request_id, "respond_to": contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})
        return response

    except psycopg2.Error as e:
        print("Error at grammarChecker API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at grammarChecker API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.get("/grammar-checker-history", tags=["grammar-checker"])
async def grammarCheckerHistory(contact_no: str = Depends(get_current_user)) -> dict:
    try:
        response = {
            "message": "Successful",
            "data": {
                "result": ""
            }
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT"
            " *"
            " FROM"
            " (SELECT"
            "     id as db_id,"
            "     content AS input,"
            "     result,"
            "     db_entry_time::time(0) AS \"request time\","
            "     db_entry_time::date AS request_date"
            "  FROM"
            "     startrek.grammar_checker_requests"
            "  WHERE"
            "     \"user\" = %s"
            "  ORDER BY"
            "     db_entry_time::date DESC,"
            "     db_entry_time::time ASC"
            "  LIMIT 5) temp"
            " ORDER BY temp.request_date ASC;",
            (contact_no,)
        )

        history = cursor.fetchall()

        cursor.close()
        conn.close()

        output = defaultdict(list)

        for record in history:
            db_id, content, result, request_time, request_date = record
            output[request_date].append({
                "id": db_id,
                "content": content,
                "result": result,
                "request_time": request_time
            })

        sorted_output = [{"request_date": date, "history": entries} for date, entries in sorted(output.items())]
        response["data"]["result"] = sorted_output
        return response

    except psycopg2.DatabaseError as e:
        print("grammarCheckerHistory | Database Error:", e)
        raise HTTPException(status_code=500, detail="Database error occurred")
    except Exception as e:
        print(f"grammarCheckerHistory | Unexpected Error: {e}")
        raise HTTPException(status_code=500, detail="Unexpected error occurred")


@app.post("/rewriter", tags=["rewriter"])
async def rewriter(body: RewriterSchema, contact_no: str = Depends(get_current_user)) -> dict:
    conn = psycopg2.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO startrek.request_logs(body, requested_by, request_time, module) VALUES (%s, %s, %s, '
        '%s) RETURNING id',
        (body.json(), contact_no, datetime.now(), "rewriter"))

    conn.commit()

    request_id = cursor.fetchone()[0]
    try:
        response = {
            "message": "Successful",
            "data": {
                "result": ""
            }
        }

        new_thread = client.beta.threads.create()

        new_thread_message = client.beta.threads.messages.create(
            thread_id=new_thread.id,
            role="user",
            content=body.content + f"\nTone: {body.tone}",
        )

        new_run = client.beta.threads.runs.create(
            thread_id=new_thread.id,
            assistant_id='asst_PUll7pgEUdNTTOc3qFys901L'
        )

        while new_run.status in ["queued", "in_progress"]:
            keep_retrieving_run = client.beta.threads.runs.retrieve(
                thread_id=new_thread.id,
                run_id=new_run.id
            )

            if keep_retrieving_run.status == "completed":

                all_messages = client.beta.threads.messages.list(
                    thread_id=new_thread.id
                )

                data = {
                    "user": contact_no,
                    "content": body.content,
                    "tone": body.tone,
                    "result": all_messages.data[0].content[0].text.value,
                    "thread_id": new_thread.id,
                    "run_id": new_run.id,
                    "requested_at": all_messages.data[1].created_at,
                    "completed_at": all_messages.data[0].created_at,
                    "prompt_tokens": keep_retrieving_run.usage.prompt_tokens,
                    "completion_tokens": keep_retrieving_run.usage.completion_tokens,
                    "total_tokens": keep_retrieving_run.usage.total_tokens,
                    "thread_message_object": new_thread_message.json(),
                    "run_object": new_run.json(),
                    "raw_response": all_messages.json()
                }

                cursor.execute(
                    'INSERT INTO startrek.rewriter_requests("user", content, tone, result, thread_id, run_id, '
                    'requested_at, completed_at, thread_message_object, run_object, raw_response, completion_tokens, '
                    'prompt_tokens, total_tokens, db_entry_time ) VALUES (%s, %s, %s, %s,'
                    '%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                    (data["user"], data["content"], data["tone"], data["result"], data["thread_id"], data["run_id"],
                     data["requested_at"],
                     data["completed_at"], data["thread_message_object"], data["run_object"], data["raw_response"],
                     data["completion_tokens"], data["prompt_tokens"], data["total_tokens"], datetime.now()))
                conn.commit()
                cursor.close()
                conn.close()

                response["data"] = {
                    "result": all_messages.data[0].content[0].text.value
                }

                await insertResponseLog(
                    {"request_id": request_id, "respond_to": data["user"], "response": json.dumps(response),
                     "response_time": datetime.now()})
                return response

            elif keep_retrieving_run.status == "queued" or keep_retrieving_run.status == "in_progress":
                pass
            else:
                break

        await insertResponseLog(
            {"request_id": request_id, "respond_to": contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})
        return response

    except psycopg2.Error as e:
        print("Error at rewriter API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at rewriter API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.get("/rewriter-history", tags=["rewriter"])
async def rewriterHistory(contact_no: str = Depends(get_current_user)) -> dict:
    try:
        response = {
            "message": "Successful",
            "data": {
                "result": ""
            }
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT"
            " *"
            " FROM"
            " (SELECT"
            "     id as db_id,"
            "     content AS input,"
            "     tone,"
            "     result,"
            "     db_entry_time::time(0) AS \"request time\","
            "     db_entry_time::date AS request_date"
            "  FROM"
            "     startrek.rewriter_requests"
            "  WHERE"
            "     \"user\" = %s"
            "  ORDER BY"
            "     db_entry_time::date DESC,"
            "     db_entry_time::time ASC"
            "  LIMIT 5) temp"
            " ORDER BY temp.request_date ASC;",
            (contact_no,)
        )
        conn.commit()

        history = cursor.fetchall()

        cursor.close()
        conn.close()

        output = []

        for record in history:
            db_id, content, tone, result, request_time, request_date = record
            index = next((i for i, item in enumerate(output) if item['request_date'] == request_date), -1)
            if index == -1:
                output.append({
                    "request_date": request_date,
                    "history": [
                        {"id": db_id, "content": content, "tone": tone, "result": result, "request_time": request_time}]
                })
            else:
                output[index]['history'].append({
                    "id": db_id,
                    "content": content,
                    "tone": tone,
                    "result": result,
                    "request_time": request_time
                })

        response["data"]["result"] = output
        return response

    except psycopg2.Error as e:
        print("Error at rewriterHistory API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at rewriterHistory API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.post("/summarizer", tags=["summarizer"])
async def summarizer(body: SummarizerSchema, contact_no: str = Depends(get_current_user)) -> dict:
    conn = psycopg2.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO startrek.request_logs(body, requested_by, request_time, module) VALUES (%s, %s, %s, '
        '%s) RETURNING id',
        (body.json(), contact_no, datetime.now(), "summarizer"))

    conn.commit()

    request_id = cursor.fetchone()[0]
    try:
        response = {
            "message": "Successful",
            "data": {
                "result": ""
            }
        }

        new_thread = client.beta.threads.create()

        content = body.content
        assistant_id = 'asst_yv6q01PfOKAYdQGkZxVYPtXt'

        if body.type == 'paragraph':
            content = body.content + f"\nThe word count of the summary text should be around {body.length or 0}"
            assistant_id = 'asst_bVXp7RlHAu8SfLhloAmRatBo'

        new_thread_message = client.beta.threads.messages.create(
            thread_id=new_thread.id,
            role="user",
            content=content,
        )

        new_run = client.beta.threads.runs.create(
            thread_id=new_thread.id,
            assistant_id=assistant_id
        )

        while new_run.status in ["queued", "in_progress"]:
            keep_retrieving_run = client.beta.threads.runs.retrieve(
                thread_id=new_thread.id,
                run_id=new_run.id
            )

            if keep_retrieving_run.status == "completed":

                all_messages = client.beta.threads.messages.list(
                    thread_id=new_thread.id
                )

                data = {
                    "user": contact_no,
                    "content": body.content,
                    "type": body.type,
                    "length": body.length if body.length is not None else None,
                    "result": all_messages.data[0].content[0].text.value,
                    "thread_id": new_thread.id,
                    "run_id": new_run.id,
                    "requested_at": all_messages.data[1].created_at,
                    "completed_at": all_messages.data[0].created_at,
                    "prompt_tokens": keep_retrieving_run.usage.prompt_tokens,
                    "completion_tokens": keep_retrieving_run.usage.completion_tokens,
                    "total_tokens": keep_retrieving_run.usage.total_tokens,
                    "thread_message_object": new_thread_message.json(),
                    "run_object": new_run.json(),
                    "raw_response": all_messages.json()
                }

                cursor.execute(
                    'INSERT INTO startrek.summarizer_requests("user", content, type, length, result, thread_id, run_id,'
                    'requested_at, completed_at, thread_message_object, run_object, raw_response, completion_tokens, '
                    'prompt_tokens, total_tokens, db_entry_time ) VALUES (%s, %s, %s, %s,'
                    '%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                    (data["user"], data["content"], data["type"], data["length"], data["result"], data["thread_id"],
                     data["run_id"],
                     data["requested_at"],
                     data["completed_at"], data["thread_message_object"], data["run_object"], data["raw_response"],
                     data["completion_tokens"], data["prompt_tokens"], data["total_tokens"], datetime.now()))
                conn.commit()
                cursor.close()
                conn.close()

                response["data"] = {
                    "result": all_messages.data[0].content[0].text.value
                }

                await insertResponseLog(
                    {"request_id": request_id, "respond_to": data["user"], "response": json.dumps(response),
                     "response_time": datetime.now()})
                return response

            elif keep_retrieving_run.status == "queued" or keep_retrieving_run.status == "in_progress":
                pass
            else:
                break

        await insertResponseLog(
            {"request_id": request_id, "respond_to": contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})
        return response

    except psycopg2.Error as e:
        print("Error at summarizer API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at summarizer API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.get("/summarizer-history", tags=["summarizer"])
async def summarizerHistory(contact_no: str = Depends(get_current_user)) -> dict:
    try:
        response = {
            "message": "Successful",
            "data": {
                "result": ""
            }
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT"
            "  * "
            "FROM"
            "    (SELECT"
            "        id AS db_id,"
            "        content AS input,"
            "        type AS summary_type,"
            "        length,"
            "        result,"
            "        db_entry_time::time(0) AS \"request_time\","
            "        db_entry_time::date AS request_date"
            "    FROM"
            "        startrek.summarizer_requests"
            "    WHERE"
            "        \"user\" = %s"
            "    ORDER BY"
            "        id DESC"
            "    LIMIT 5) temp "
            "ORDER BY temp.request_time ASC;",
            (contact_no,)
        )
        conn.commit()

        history = cursor.fetchall()

        cursor.close()
        conn.close()

        output = []

        for record in history:
            db_id, content, summary_type, length, result, request_time, request_date = record
            index = next((i for i, item in enumerate(output) if item['request_date'] == request_date), -1)
            if index == -1:
                output.append({
                    "request_date": request_date,
                    "history": [
                        {"id": db_id, "content": content, "type": summary_type, "length": length, "result": result,
                         "request_time": request_time}]
                })
            else:
                output[index]['history'].append({
                    "id": db_id,
                    "content": content,
                    "type": summary_type,
                    "length": length,
                    "result": result,
                    "request_time": request_time
                })

        response["data"]["result"] = output
        return response

    except psycopg2.Error as e:
        print("Error at summarizerHistory API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at summarizerHistory API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.post("/generate-cv", tags=["cv-generator"])
async def cvGenerator(body: CvGeneratorSchema.RealSchema, contact_no: str = Depends(get_current_user)) -> dict:
    conn = psycopg2.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO startrek.request_logs(body, requested_by, request_time, module) VALUES (%s, %s, %s, '
        '%s) RETURNING id',
        (body.json(), contact_no, datetime.now(), "cv-generator"))

    conn.commit()

    request_id = cursor.fetchone()[0]

    try:
        response = {
            "message": "Successful",
            "data": {
                "result": ""
            }
        }

        keywords = {"career_objective_keywords": body.career_objective_keywords, "work_expertise_keywords": [],
                    "co_curricular_activities": body.co_curricular_activities, "skills": body.skills}

        career_objective_additional_info = await careerObjectiveResponse(body.career_objective_keywords,
                                                                         body.career_objective_tone)

        cv_data = {
            "created_by": contact_no,
            "contact_no": body.contact_no,
            "email": body.email,
            "career_objective_keywords": body.career_objective_keywords,
            "career_objective_tone": body.career_objective_tone,
            "career_objective_generative_response": career_objective_additional_info["result"],
            "linkedin": body.linkedin,
            "address": body.address,
            "skills": body.skills,
            "co_curricular_activities": body.co_curricular_activities,
            "image": body.image,
            "template": body.template,
            "full_name": body.full_name,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
            "career_objective_additional_info": career_objective_additional_info
        }

        cursor.execute(
            'INSERT INTO startrek.cv_requests('
            'created_by, contact_no, email, career_objective_keywords, career_objective_tone, '
            'career_objective_generative_response, linkedin, address, skills, co_curricular_activities, '
            'image, template, full_name, created_at, updated_at, career_objective_additional_info'
            ') VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id',
            (
                cv_data["created_by"],
                cv_data["contact_no"],
                cv_data["email"],
                cv_data["career_objective_keywords"],
                cv_data["career_objective_tone"],
                cv_data["career_objective_generative_response"],
                cv_data["linkedin"],
                cv_data["address"],
                cv_data["skills"],
                cv_data["co_curricular_activities"],
                cv_data["image"],
                cv_data["template"],
                cv_data["full_name"],
                cv_data["created_at"],
                cv_data["updated_at"],
                json.dumps(cv_data["career_objective_additional_info"])  # Convert the dictionary to a JSON string
            )
        )

        conn.commit()
        cv_id = cursor.fetchone()[0]

        working_experiences = []

        if len(body.working_experiences) > 0:
            for experience in body.working_experiences:
                keywords["work_expertise_keywords"].append(experience.work_expertise_keywords)
                experience_dict = experience.dict()
                experience_dict["cv_id"] = cv_id
                experience_dict["end_date"] = experience.end_date if experience.end_date is not None else None
                temp = f"{experience_dict['start_date']} - " \
                       f"{experience.end_date if experience.end_date is not None else date.today()}"
                work_expertise_generative_response = await workingExperienceResponse(experience.work_expertise_keywords,
                                                                                     experience.work_expertise_tone,
                                                                                     experience.org_name,
                                                                                     experience.designation, temp)
                experience_dict["work_expertise_generative_response"] = work_expertise_generative_response["result"]
                experience_dict["work_expertise_additional_info"] = work_expertise_generative_response
                working_experiences.append(experience_dict)

            args_str = ','.join(
                cursor.mogrify(
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        experience["org_name"],
                        experience["designation"],
                        experience["start_date"],
                        experience["end_date"],
                        experience["work_expertise_keywords"],
                        experience["work_expertise_tone"],
                        experience["work_expertise_generative_response"],
                        experience["cv_id"],
                        json.dumps(experience["work_expertise_additional_info"])
                    )
                ).decode('utf-8')
                for experience in working_experiences
            )
            cursor.execute(
                "INSERT INTO startrek.cv_work_experiences ("
                "org_name, designation, start_date, end_date, "
                "work_expertise_keywords, work_expertise_tone, "
                "work_expertise_generative_response, cv_id, work_expertise_additional_info"
                ") VALUES " + args_str
            )
            conn.commit()

        education_records = []

        for education in body.educations:
            education_dict = education.dict()
            education_dict["cv_id"] = cv_id
            education_records.append(education_dict)

        args_str = ','.join(
            cursor.mogrify(
                "(%s,%s,%s,%s,%s)",
                (
                    education["education_level"],
                    education["institute_name"],
                    education["starting_year"],
                    education["passing_year"],
                    education["cv_id"]
                )
            ).decode('utf-8')
            for education in education_records
        )

        cursor.execute(
            "INSERT INTO startrek.cv_educations ("
            "education_level, institute_name, starting_year, passing_year, cv_id"
            ") VALUES " + args_str
        )
        conn.commit()

        if len(body.references) > 0:
            reference_records = []

            for reference in body.references:
                reference_dict = reference.dict()
                reference_dict["cv_id"] = cv_id
                reference_records.append(reference_dict)

            args_str = ','.join(
                cursor.mogrify(
                    "(%s,%s,%s,%s,%s,%s)",
                    (
                        reference["full_name"],
                        reference["designation"],
                        reference["contact_no"],
                        reference["email"],
                        reference["company"],
                        reference["cv_id"]
                    )
                ).decode('utf-8')
                for reference in reference_records
            )

            cursor.execute(
                "INSERT INTO startrek.cv_references ("
                "full_name, designation, contact_no, email, company, cv_id"
                ") VALUES " + args_str
            )
            conn.commit()

        cursor.close()
        conn.close()

        links = await makeDocFromTemplate(body, cv_id, working_experiences, career_objective_additional_info["result"],
                                          body.template)
        suggestionEngine(keywords)

        response["data"] = links

        await insertResponseLog(
            {"request_id": request_id, "respond_to": contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})
        return response

    except psycopg2.Error as e:
        print("Error at cvGenerator API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        content = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        print(f"Error at CV Generator: {content}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.post("/upload-cv-pic", tags=["cv-generator"])
async def uploadCvProfilePic(image: UploadFile = File(...), contact_no: str = Depends(get_current_user)) -> dict:
    try:
        file_extension = image.filename.split(".")[-1]
        unique_filename = f"{uuid.uuid4()}.{file_extension}"

        s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION
        )

        s3_client.upload_fileobj(
            image.file,
            S3_BUCKET_NAME,
            f"startrek/{os.getenv('stage')}/user-{contact_no}/cv/{unique_filename}",
            ExtraArgs={"ContentType": image.content_type}
        )

        return {
            "message": "Successful",
            "data": {
                "result": f"startrek/{S3_BUCKET_NAME}/{os.getenv('stage')}/user-{contact_no}/cv/{unique_filename}"
            }
        }
    except NoCredentialsError:
        raise HTTPException(status_code=404, detail="AWS credentials not found")
    except PartialCredentialsError:
        raise HTTPException(status_code=404, detail="Incomplete AWS credentials")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/cv-history", tags=["cv-generator"])
async def cvGeneratorHistory(contact_no: str = Depends(get_current_user)):
    try:
        response = {
            "message": "Successful",
            "data": None
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO startrek.request_logs(requested_by, request_time, module) VALUES (%s, %s, '
            '%s) RETURNING id',
            (contact_no, datetime.now(), "cv-history"))

        conn.commit()

        request_id = cursor.fetchone()[0]

        query = '''
        SELECT
            cl.cv_id,
            cr.full_name || '_' || cl.id AS cv_title,
            TO_CHAR(cr.created_at, 'DD Mon YYYY') AS created_date,
            TO_CHAR(cr.created_at, 'hh12:mi:ss AM') AS created_time,
            cl.seen,
            cl.docx_edit_link,
            cl.docx_download_link,
            cl.pdf_download_link,
            cl.pdf_view_link
        FROM
            startrek.cv_links cl
        JOIN
            startrek.cv_requests cr
        ON
            cl.cv_id = cr.id
        WHERE
            cr.created_by = %s
            AND cr.is_deleted = false
        ORDER BY
            cl.id DESC
        LIMIT 5
        '''

        params = (contact_no,)

        cursor.execute(query, params)
        history = cursor.fetchall()

        cursor.close()
        conn.close()

        output = []

        for record in history:
            cv_id, cv_title, created_date, created_time, seen, docx_edit_link, docx_download_link, pdf_download_link, \
                pdf_view_link = record
            output.append({
                "cv_id": cv_id,
                "cv_title": cv_title,
                "created_date": created_date,
                "created_time": created_time,
                "seen": seen,
                "docx_edit_link": docx_edit_link,
                "docx_download_link": docx_download_link,
                "pdf_download_link": pdf_download_link,
                "pdf_view_link": pdf_view_link
            })

        response["data"] = output

        await insertResponseLog(
            {"request_id": request_id, "respond_to": contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})

        return response

    except psycopg2.Error as e:
        print("Error at cv-history API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at cv-history API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.patch("/cv-seen", tags=["cv-generator"])
async def cvSeenUpdate(cv_id: str, contact_no: str = Depends(get_current_user)):
    try:
        response = {
            "message": "Successful",
            "data": None
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO startrek.request_logs(body, requested_by, request_time, module) VALUES (%s, %s, %s, '
            '%s) RETURNING id',
            (json.dumps({"cv_id": cv_id}), contact_no, datetime.now(), "cv-seen"))

        conn.commit()

        request_id = cursor.fetchone()[0]

        query = '''
        UPDATE
            startrek.cv_links
        SET
            seen = TRUE
        WHERE
            cv_id = %s;
        '''

        params = (cv_id,)

        cursor.execute(query, params)
        conn.commit()

        cursor.close()
        conn.close()

        await insertResponseLog(
            {"request_id": request_id, "respond_to": contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})

        return response

    except psycopg2.Error as e:
        print("Error at cv-history API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at cv-history API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.delete("/cv-delete", tags=["cv-generator"])
async def cvDelete(cv_id: str, contact_no: str = Depends(get_current_user)):
    try:
        response = {
            "message": "Successful",
            "data": None
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO startrek.request_logs(body, requested_by, request_time, module) VALUES (%s, %s, %s, '
            '%s) RETURNING id',
            (json.dumps({"cv_id": cv_id}), contact_no, datetime.now(), "cv-delete"))

        conn.commit()

        request_id = cursor.fetchone()[0]

        query = '''
        UPDATE
            startrek.cv_requests
        SET
            is_deleted = TRUE
        WHERE
            id = %s;
        '''

        params = (cv_id,)

        cursor.execute(query, params)
        conn.commit()

        cursor.close()
        conn.close()

        await insertResponseLog(
            {"request_id": request_id, "respond_to": contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})

        return response

    except psycopg2.Error as e:
        print("Error at cv-history API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at cv-history API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.get("/cv-templates", tags=["cv-generator"])
async def getCVtemplates(contact_no: str = Depends(get_current_user)):
    try:
        response = {
            "message": "Successful",
            "data": None
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO startrek.request_logs(requested_by, request_time, module) VALUES (%s, %s, '
            '%s) RETURNING id',
            (contact_no, datetime.now(), "cv-templates"))

        conn.commit()

        request_id = cursor.fetchone()[0]

        query = '''
        SELECT
            id as db_id, name, image
        FROM
            startrek.cv_templates
        WHERE
            is_deleted = false
        '''

        cursor.execute(query)
        history = cursor.fetchall()

        cursor.close()
        conn.close()

        output = []

        for record in history:
            db_id, name, image = record
            output.append({
                "id": db_id,
                "name": name,
                "image": image
            })

        response["data"] = output

        await insertResponseLog(
            {"request_id": request_id, "respond_to": contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})

        return response

    except psycopg2.Error as e:
        print("Error at cv-templates API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at cv-templates API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.get("/cv-view/{cv_id}", tags=["cv-generator"])
async def getCV(cv_id: int, contact_no: str = Depends(get_current_user)):
    try:
        response = {
            "message": "Successful",
            "data": None
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO startrek.request_logs(body, requested_by, request_time, module) VALUES (%s, %s, %s, '
            '%s) RETURNING id',
            (json.dumps({"cv_id": cv_id}), contact_no, datetime.now(), "cv-view"))

        conn.commit()

        request_id = cursor.fetchone()[0]

        query = '''
        SELECT
            json_build_object(
                'full_name', cr.full_name,
                'contact_no', cr.contact_no,
                'email', cr.email,
                'career_objective_keywords', cr.career_objective_keywords,
                'career_objective_tone', cr.career_objective_tone,
                'linkedin', cr.linkedin,
                'address', cr.address,
                'skills', cr.skills,
                'co_curricular_activities', cr.co_curricular_activities
            ) AS personal_information,
            json_build_object(
                'image', cr.image
            ) AS others,
            (SELECT jsonb_agg(
                    json_build_object(
                        'institute_name', ce.institute_name,
                        'education_level', ce.education_level,
                        'starting_year', ce.starting_year,
                        'passing_year', ce.passing_year
                    )
                )
             FROM startrek.cv_educations ce
             WHERE ce.cv_id = cr.id
            ) AS educations,
            (SELECT jsonb_agg(
                    json_build_object(
                        'org_name', cwe.org_name,
                        'designation', cwe.designation,
                        'start_date', cwe.start_date,
                        'end_date', cwe.end_date,
                        'work_expertise_keywords', cwe.work_expertise_keywords,
                        'work_expertise_tone', cwe.work_expertise_tone
                    )
                )
             FROM startrek.cv_work_experiences cwe
             WHERE cwe.cv_id = cr.id
            ) AS working_experiences,
            (SELECT jsonb_agg(
                    json_build_object(
                        'full_name', cre.full_name,
                        'designation', cre.designation,
                        'company', cre.company,
                        'contact_no', cre.contact_no,
                        'email', cre.email
                    )
                )
             FROM startrek.cv_references cre
             WHERE cre.cv_id = cr.id
            ) AS references,
            cr.template
        FROM
            startrek.cv_requests cr
        WHERE
            cr.id = %s
            AND cr.created_by = %s;
        '''

        cursor.execute(query, (cv_id, contact_no,))
        result = cursor.fetchone()

        cursor.close()
        conn.close()

        output = []

        if result:
            personal_information, others, educations, working_experiences, references, template = result
            output = {
                "personal_information": personal_information,
                "others": others,
                "educations": educations,
                "working_experiences": working_experiences if working_experiences else [],
                "references": references if references else [],
                "template": template
            }
            response["data"] = output
        else:
            response["data"] = None

        response["data"] = output

        await insertResponseLog(
            {"request_id": request_id, "respond_to": contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})

        return response

    except psycopg2.Error as e:
        print("Error at cv-templates API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at cv-templates API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.get("/get-suggestions", tags=["cv-generator"])
async def getSuggestions(contact_no: str = Depends(get_current_user)):
    try:
        response = {
            "message": "Successful",
            "data": None
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO startrek.request_logs(requested_by, request_time, module) VALUES (%s, %s, '
            '%s) RETURNING id',
            (contact_no, datetime.now(), "cv-view"))

        conn.commit()

        request_id = cursor.fetchone()[0]

        query = '''
                SELECT
                    co_occurance_frequency
                FROM
                    startrek.keywords_frequencies
                WHERE
                    id = 1
                '''

        cursor.execute(query)
        result = cursor.fetchone()[0]

        cursor.close()
        conn.close()
        response["data"] = result
        await insertResponseLog(
            {"request_id": request_id, "respond_to": contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})

        return response

    except psycopg2.Error as e:
        print("Error at cv-templates API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at cv-templates API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.post("/response-generator", tags=["response-generator"])
async def responseGenerator(body: ResponseGeneratorSchema, contact_no: str = Depends(get_current_user)) -> dict:
    conn = psycopg2.connect(conn_str)
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO startrek.request_logs(body, requested_by, request_time, module) VALUES (%s, %s, %s, '
        '%s) RETURNING id',
        (body.json(), contact_no, datetime.now(), "response-generator"))

    conn.commit()

    request_id = cursor.fetchone()[0]

    try:
        response = {
            "message": "Successful",
            "data": {
                "result": ""
            }
        }

        new_thread = client.beta.threads.create()

        template = (
            "Here is the received message\n"
            "{received_message}\n"
            "\n"
            "Here is the response outline\n"
            "{response_outline}\n"
            "\n"
            "The tone should be {tone}\n"
        )

        new_thread_message = client.beta.threads.messages.create(
            thread_id=new_thread.id,
            role="user",
            content=template.format(
                received_message=body.content,
                response_outline=body.outline,
                tone=body.tone
            ),
        )

        new_run = client.beta.threads.runs.create(
            thread_id=new_thread.id,
            assistant_id='asst_1Xwl6TI9LAAnEDBsNuo0LRAF'
        )

        while new_run.status in ["queued", "in_progress"]:
            keep_retrieving_run = client.beta.threads.runs.retrieve(
                thread_id=new_thread.id,
                run_id=new_run.id
            )

            if keep_retrieving_run.status == "completed":

                all_messages = client.beta.threads.messages.list(
                    thread_id=new_thread.id
                )

                data = {
                    "user": contact_no,
                    "content": body.content,
                    "tone": body.tone,
                    "outline": body.outline,
                    "result": all_messages.data[0].content[0].text.value,
                    "thread_id": new_thread.id,
                    "run_id": new_run.id,
                    "requested_at": all_messages.data[1].created_at,
                    "completed_at": all_messages.data[0].created_at,
                    "prompt_tokens": keep_retrieving_run.usage.prompt_tokens,
                    "completion_tokens": keep_retrieving_run.usage.completion_tokens,
                    "total_tokens": keep_retrieving_run.usage.total_tokens,
                    "thread_message_object": new_thread_message.json(),
                    "run_object": new_run.json(),
                    "raw_response": all_messages.json()
                }

                cursor.execute(
                    'INSERT INTO startrek.response_generator_requests("user", content, tone, outline, result, '
                    'thread_id,'
                    'run_id,'
                    'requested_at, completed_at, thread_message_object, run_object, raw_response, completion_tokens, '
                    'prompt_tokens, total_tokens ) VALUES (%s, %s, %s, %s, %s,'
                    '%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                    (data["user"], data["content"], data["tone"], data["outline"], data["result"], data["thread_id"],
                     data["run_id"],
                     data["requested_at"],
                     data["completed_at"], data["thread_message_object"], data["run_object"], data["raw_response"],
                     data["completion_tokens"], data["prompt_tokens"], data["total_tokens"]))

                conn.commit()
                cursor.close()
                conn.close()

                response["data"] = {
                    "result": all_messages.data[0].content[0].text.value
                }

                await insertResponseLog(
                    {"request_id": request_id, "respond_to": data["user"], "response": json.dumps(response),
                     "response_time": datetime.now()})
                return response

            elif keep_retrieving_run.status == "queued" or keep_retrieving_run.status == "in_progress":
                pass
            else:
                break

        await insertResponseLog(
            {"request_id": request_id, "respond_to": contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})
        return response

    except psycopg2.Error as e:
        print("Error at responseGenerator API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at responseGenerator API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.get("/response-generator-history", tags=["response-generator"])
async def responseGeneratorHistory(contact_no: str = Depends(get_current_user)) -> dict:
    try:
        response = {
            "message": "Successful",
            "data": {
                "result": ""
            }
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT"
            " *"
            " FROM"
            " (SELECT"
            "     id as db_id,"
            "     content AS input,"
            "     tone,"
            "     result,"
            "     db_entry_time::time(0) AS \"request time\","
            "     db_entry_time::date AS request_date"
            "  FROM"
            "     startrek.response_generator_requests"
            "  WHERE"
            "     \"user\" = %s"
            "  ORDER BY"
            "     db_entry_time::date DESC,"
            "     db_entry_time::time ASC"
            "  LIMIT 5) temp"
            " ORDER BY temp.request_date ASC;",
            (contact_no,)
        )
        conn.commit()

        history = cursor.fetchall()

        cursor.close()
        conn.close()

        output = []

        for record in history:
            db_id, content, tone, result, request_time, request_date = record
            index = next((i for i, item in enumerate(output) if item['request_date'] == request_date), -1)
            if index == -1:
                output.append({
                    "request_date": request_date,
                    "history": [
                        {"id": db_id, "content": content, "tone": tone, "result": result, "request_time": request_time}]
                })
            else:
                output[index]['history'].append({
                    "id": db_id,
                    "content": content,
                    "tone": tone,
                    "result": result,
                    "request_time": request_time
                })

        response["data"]["result"] = output
        return response

    except psycopg2.Error as e:
        print("Error at responseGeneratorHistory API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at responseGeneratorHistory API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.post("/avataryze", tags=["avataryze"])
async def avataryze(body: AvataryzeSchema, contact_no: str = Depends(get_current_user)):
    try:
        response = {
            "message": "Successful",
            "data": None
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()

        cursor.execute(
            'INSERT INTO startrek.avataryze_requests(contact_no, source_image, target_template, request_time) VALUES '
            '(%s, %s, %s,'
            '%s) RETURNING id',
            (contact_no, body.source_image, body.target_template, datetime.now()))

        conn.commit()

        request_id = cursor.fetchone()[0]

        generated_images = [
            "https://ppol-web-uploads.s3.ap-southeast-1.amazonaws.com/startrek/develop/user-01946094342/avataryze"
            "/request_1/OneByCosC_sleepless_nights_with_a_head_full_of_sad_chemicals_89726ea7-6da8-48d8-bbe7"
            "-3ae8d52de24f.png",
            "https://ppol-web-uploads.s3.ap-southeast-1.amazonaws.com/startrek/develop/user-01946094342/avataryze"
            "/request_1/OneByCosC_sleepless_nights_with_a_head_full_of_sad_chemicals_a3d338bd-59ca-4400-b64e"
            "-f2ddc5b04994.png",
            "https://ppol-web-uploads.s3.ap-southeast-1.amazonaws.com/startrek/develop/user-01946094342/avataryze"
            "/request_1/OneByCosC_sleepless_nights_with_a_head_full_of_sad_chemicals_dd8d15b6-565f-497c-b146"
            "-ffc59ad92043.png",
            "https://ppol-web-uploads.s3.ap-southeast-1.amazonaws.com/startrek/develop/user"
            "-01946094342/avataryze/request_1"
            "/OneByCosC_vaping_while_walking_through_a_barn_which_has_scarecr_81ae6343-8003-4acf-8683-b80ee3457cdd.png"
        ]

        args_str = ','.join(
            cursor.mogrify("(%s, %s, %s)", (request_id, image, datetime.now())).decode('utf-8')
            for image in generated_images
        )

        cursor.execute(
            "INSERT INTO startrek.avataryze_responses (request_id, image_url, response_time) VALUES " + args_str
        )

        conn.commit()

        cursor.close()
        conn.close()
        response["data"] = generated_images

        await insertResponseLog(
            {"request_id": request_id, "respond_to": contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})

        return response

    except psycopg2.Error as e:
        print("Error at cv-templates API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at cv-templates API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.post("/avataryze/upload-generated-images", tags=["avataryze"])
async def uploadAvataryzedImages(
        images: List[UploadFile] = File(...),
        contact_no: str = Depends(get_current_user)
) -> dict:
    try:
        s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION
        )

        uploaded_images = []

        for image in images:
            file_extension = image.filename.split(".")[-1]
            unique_filename = f"{uuid.uuid4()}.{file_extension}"

            s3_client.upload_fileobj(
                image.file,
                S3_BUCKET_NAME,
                f"startrek/{os.getenv('stage')}/user-{contact_no}/cv/{unique_filename}",
                ExtraArgs={"ContentType": image.content_type}
            )

            uploaded_images.append(
                f"startrek/{S3_BUCKET_NAME}/{os.getenv('stage')}/user-{contact_no}/cv/{unique_filename}")

        return {
            "message": "Successful",
            "data": {
                "results": uploaded_images
            }
        }
    except NoCredentialsError:
        raise HTTPException(status_code=404, detail="AWS credentials not found")
    except PartialCredentialsError:
        raise HTTPException(status_code=404, detail="Incomplete AWS credentials")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/avataryze/upload-source-image", tags=["avataryze"])
async def uploadAvataryzeSourceImage(image: UploadFile = File(...),
                                     contact_no: str = Depends(get_current_user)) -> dict:
    try:
        file_extension = image.filename.split(".")[-1]
        unique_filename = f"{uuid.uuid4()}.{file_extension}"

        s3_client = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION
        )

        s3_client.upload_fileobj(
            image.file,
            S3_BUCKET_NAME,
            f"startrek/{os.getenv('stage')}/user-{contact_no}/avataryze/source_images/{unique_filename}",
            ExtraArgs={"ContentType": image.content_type}
        )

        return {
            "message": "Successful",
            "data": {
                "result": f"startrek/{os.getenv('stage')}/user-{contact_no}/avataryze/source_images/{unique_filename}"
            }
        }
    except NoCredentialsError:
        raise HTTPException(status_code=404, detail="AWS credentials not found")
    except PartialCredentialsError:
        raise HTTPException(status_code=404, detail="Incomplete AWS credentials")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/avataryze/history", tags=["avataryze"])
async def avataryzeHistory(contact_no: str = Depends(get_current_user)):
    try:
        response = {
            "message": "Successful",
            "data": None
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()

        cursor.execute(
            'INSERT INTO startrek.request_logs(requested_by, request_time, module) VALUES (%s, %s, %s) RETURNING id',
            (contact_no, datetime.now(), "avataryze-history")
        )
        conn.commit()
        request_id = cursor.fetchone()[0]

        query = '''
            SELECT
                arq.request_time::date AS request_date,
                jsonb_agg(
                    json_build_object('id', ars.id, 'image_url', ars.image_url)
                ) AS generated_image
            FROM
                startrek.avataryze_requests arq
            JOIN
                startrek.avataryze_responses ars ON arq.id = ars.request_id
            WHERE
                arq.contact_no = %s
                AND ars.is_deleted = false
            GROUP BY
                arq.request_time::date
            ORDER BY
                arq.request_time::date DESC
            LIMIT 5;
        '''
        cursor.execute(query, (contact_no,))
        results = cursor.fetchall()

        formatted_result = []
        for result in results:
            request_date, generated_image = result
            formatted_result.append({
                "request_date": str(request_date),
                "history": generated_image
            })

        response["data"] = formatted_result if formatted_result else []

        cursor.close()
        conn.close()

        await insertResponseLog({
            "request_id": request_id,
            "respond_to": contact_no,
            "response": json.dumps(response),
            "response_time": datetime.now()
        })

        return response

    except psycopg2.Error as e:
        print("Database Error at Avataryze History API:", e)
        raise HTTPException(status_code=500, detail=str(e))

    except Exception as e:
        print(f"Error at Avataryze Historyy API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.delete("/avataryze/delete", tags=["avataryze"])
async def avataryzeDelete(image_id: int, contact_no: str = Depends(get_current_user)):
    try:
        response = {
            "message": "Successful",
            "data": None
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()

        cursor.execute(
            'INSERT INTO startrek.request_logs(body, requested_by, request_time, module) VALUES (%s, %s, %s, '
            '%s) RETURNING id',
            (json.dumps({
                image_id: image_id
            }), contact_no, datetime.now(), "avataryze-delete")
        )

        conn.commit()
        request_id = cursor.fetchone()[0]

        query = '''
            UPDATE
                startrek.avataryze_responses
            SET
                is_deleted = true
            WHERE
                id = %s
        '''
        cursor.execute(query, (image_id,))
        conn.commit()

        cursor.close()
        conn.close()

        await insertResponseLog({
            "request_id": request_id,
            "respond_to": contact_no,
            "response": json.dumps(response),
            "response_time": datetime.now()
        })

        return response

    except psycopg2.Error as e:
        print("Database Error at Avataryze History API:", e)
        raise HTTPException(status_code=500, detail=str(e))

    except Exception as e:
        print(f"Error at Avataryze Historyy API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.get("/avataryze/template-list", tags=["avataryze"])
async def avataryzeTemplateList(contact_no: str = Depends(get_current_user)):
    try:
        response = {
            "message": "Successful",
            "data": None
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()

        cursor.execute(
            'INSERT INTO startrek.request_logs(requested_by, request_time, module) VALUES (%s, %s, %s) RETURNING id',
            (contact_no, datetime.now(), "cv-history")
        )

        conn.commit()
        request_id = cursor.fetchone()[0]

        query = '''
            SELECT
                *
            FROM
                startrek.avataryze_templates
        '''
        cursor.execute(query)
        results = cursor.fetchall()

        formatted_data = [
            {
                "id": item[0],
                "name": item[1],
                "primary_color": item[2],
                "secondary_color": item[3],
                "image_url": item[4]
            }
            for item in results
        ]

        response["data"] = formatted_data if formatted_data else []

        cursor.close()
        conn.close()

        await insertResponseLog({
            "request_id": request_id,
            "respond_to": contact_no,
            "response": json.dumps(response),
            "response_time": datetime.now()
        })

        return response

    except psycopg2.Error as e:
        print("Database Error at Avataryze History API:", e)
        raise HTTPException(status_code=500, detail=str(e))

    except Exception as e:
        print(f"Error at Avataryze Historyy API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


@app.post("/get-token", tags=["auth"])
async def user_login(loginParams: UserLoginSchema = Body(...)):
    conn = psycopg2.connect(conn_str)
    cursor = conn.cursor()

    cursor.execute(
        'INSERT INTO startrek.request_logs(body, requested_by, request_time, module) VALUES (%s, %s, %s, '
        '%s) RETURNING id',
        (loginParams.json(), loginParams.contact_no, datetime.now(), "get-token"))

    conn.commit()

    request_id = cursor.fetchone()[0]

    response = {
        "message": "Username or Password is incorrect",
        "data": None
    }

    cursor.execute("""SELECT * FROM startrek.sdk_users WHERE username = %s and is_active = TRUE;""",
                   (loginParams.username,))
    conn.commit()

    userExist = cursor.fetchone()

    cursor.close()
    conn.close()

    if not userExist:
        await insertResponseLog(
            {"request_id": request_id, "respond_to": loginParams.contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})
        return response

    if bcrypt.checkpw(loginParams.password.encode('utf-8'), userExist[2].encode('utf-8')):
        token = sign_jwt(loginParams.contact_no)

        response = {
            "message": "Successful",
            "data": {
                "token": token["access_token"]
            }
        }
        await insertResponseLog(
            {"request_id": request_id, "respond_to": loginParams.contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})
        return response

    else:
        await insertResponseLog(
            {"request_id": request_id, "respond_to": loginParams.contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})
        return response


@app.get("/validate-token", tags=["auth"])
async def validate_token(contact_no: str = Depends(get_current_user)):
    try:
        response = {
            "message": "Successful",
            "data": None
        }

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO startrek.request_logs(requested_by, request_time, module) VALUES (%s, %s, '
            '%s) RETURNING id',
            (contact_no, datetime.now(), "validate-token"))

        conn.commit()

        request_id = cursor.fetchone()[0]

        cursor.close()
        conn.close()

        await insertResponseLog(
            {"request_id": request_id, "respond_to": contact_no, "response": json.dumps(response),
             "response_time": datetime.now()})

        return response

    except psycopg2.Error as e:
        print("Error at validate_token API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at validate_token API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


async def insertResponseLog(data):
    try:
        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO startrek.response_logs(request_id, respond_to, response, response_time) VALUES (%s, %s, '
            '%s, %s)',
            (data["request_id"], data["respond_to"], data["response"], data["response_time"]))

        conn.commit()
        cursor.close()
        conn.close()
        return

    except psycopg2.Error as e:
        print("Error at insertResponseLog API")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        print(f"Error at insertResponseLog API: {e}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


async def careerObjectiveResponse(career_objective_keywords, career_objective_tone):
    try:
        new_thread = client.beta.threads.create()

        new_thread_message = client.beta.threads.messages.create(
            thread_id=new_thread.id,
            role="user",
            content=f"Keywords: {career_objective_keywords} Tone: {career_objective_tone}"
        )

        new_run = client.beta.threads.runs.create(
            thread_id=new_thread.id,
            assistant_id='asst_lRr4UmF5huxfSpymWSsQuZnB'
        )

        while new_run.status in ["queued", "in_progress"]:
            keep_retrieving_run = client.beta.threads.runs.retrieve(
                thread_id=new_thread.id,
                run_id=new_run.id
            )

            if keep_retrieving_run.status == "completed":
                all_messages = client.beta.threads.messages.list(
                    thread_id=new_thread.id
                )
                return {
                    "result": all_messages.data[0].content[0].text.value,
                    "thread_id": new_thread.id,
                    "run_id": new_run.id,
                    "requested_at": all_messages.data[1].created_at,
                    "completed_at": all_messages.data[0].created_at,
                    "prompt_tokens": keep_retrieving_run.usage.prompt_tokens,
                    "completion_tokens": keep_retrieving_run.usage.completion_tokens,
                    "total_tokens": keep_retrieving_run.usage.total_tokens,
                    "thread_message_object": new_thread_message.json(),
                    "run_object": new_run.json(),
                    "raw_response": all_messages.json()
                }

    except Exception as e:
        print(f"Error Generating Career Objective AI Response: {e}")
        return {
            "result": "I want to succeed in an environment of growth and excellence to meet personal and "
                      "organizational goals",
        }
        # raise HTTPException(status_code=500, detail="Connection Failed!")


async def workingExperienceResponse(work_expertise_keywords, work_expertise_tone, company_name, designation, duration):
    try:
        new_thread = client.beta.threads.create()

        new_thread_message = client.beta.threads.messages.create(
            thread_id=new_thread.id,
            role="user",
            content=f"Keywords: {work_expertise_keywords} Tone: {work_expertise_tone} Company: {company_name} "
                    f"Duration: {duration} Designation: {designation}"
        )

        print(
            f"Keywords: {work_expertise_keywords} Tone: {work_expertise_tone} Company: {company_name} "
            f"Duration: {duration} Designation: {designation}")

        new_run = client.beta.threads.runs.create(
            thread_id=new_thread.id,
            assistant_id='asst_3HPVYDoGJ9B5dOsuoVgnAWCF'
        )

        while new_run.status in ["queued", "in_progress"]:
            keep_retrieving_run = client.beta.threads.runs.retrieve(
                thread_id=new_thread.id,
                run_id=new_run.id
            )

            if keep_retrieving_run.status == "completed":
                all_messages = client.beta.threads.messages.list(
                    thread_id=new_thread.id
                )

                return {
                    "result": all_messages.data[0].content[0].text.value,
                    "thread_id": new_thread.id,
                    "run_id": new_run.id,
                    "requested_at": all_messages.data[1].created_at,
                    "completed_at": all_messages.data[0].created_at,
                    "prompt_tokens": keep_retrieving_run.usage.prompt_tokens,
                    "completion_tokens": keep_retrieving_run.usage.completion_tokens,
                    "total_tokens": keep_retrieving_run.usage.total_tokens,
                    "thread_message_object": new_thread_message.json(),
                    "run_object": new_run.json(),
                    "raw_response": all_messages.json()
                }

    except Exception as e:
        print(f"Error Generating Career Objective AI Response: {e}")
        return {
            "result": "Couldn't Generate",
        }
        # raise HTTPException(status_code=500, detail="Connection Failed!")


async def makeDocFromTemplate(data, cv_id, working_experiences, career_objective, template):
    global template_response
    SCOPES = ['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']

    credentials = service_account.Credentials.from_service_account_file(
        "service_account.json", scopes=SCOPES)
    docs_service = build('docs', 'v1', credentials=credentials)
    service = build("drive", "v3", credentials=credentials)

    output_filename = f"{data.full_name}_{time.time()}"

    if template == 1:
        template_response = await cvtemplate_1(service, docs_service, data, working_experiences, career_objective)
    elif template == 2:
        template_response = await cvtemplate_2(service, docs_service, data, working_experiences, career_objective)
    try:
        docs_service.documents().batchUpdate(
            documentId=template_response["new_doc_id"],
            body={'requests': template_response["requests"]}
        ).execute()

        pdf_request = service.files().export_media(fileId=template_response["new_doc_id"], mimeType='application/pdf')
        pdf_data = io.BytesIO()
        pdf_downloader = MediaIoBaseDownload(pdf_data, pdf_request)
        done = False
        while not done:
            status, done = pdf_downloader.next_chunk()
        pdf_data.seek(0)

        pdf_file_metadata = {
            'name': f"{output_filename}.pdf",
            'mimeType': 'application/pdf'
        }

        pdf_media = MediaIoBaseUpload(pdf_data, mimetype='application/pdf')

        pdf_file = service.files().create(body=pdf_file_metadata, media_body=pdf_media, fields='id').execute()

        pdf_permission = {
            'type': 'anyone',
            'role': 'reader',
        }
        service.permissions().create(
            fileId=pdf_file.get('id'),
            body=pdf_permission,
        ).execute()

        docx_request = service.files().export_media(fileId=template_response["new_doc_id"],
                                                    mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        docx_data = io.BytesIO()
        docx_downloader = MediaIoBaseDownload(docx_data, docx_request)
        done = False
        while not done:
            status, done = docx_downloader.next_chunk()
        docx_data.seek(0)

        docx_file_metadata = {
            'name': f"{output_filename}.docx",
            'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        }

        docx_media = MediaIoBaseUpload(docx_data,
                                       mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

        docx_file = service.files().create(body=docx_file_metadata, media_body=docx_media, fields='id').execute()

        docx_permission = {
            'type': 'anyone',
            'role': 'reader',
        }
        service.permissions().create(
            fileId=docx_file.get('id'),
            body=docx_permission,
        ).execute()

        conn = psycopg2.connect(conn_str)
        cursor = conn.cursor()

        cursor.execute(
            'INSERT INTO startrek.cv_links('
            'cv_id, docx_edit_link, docx_download_link, pdf_download_link, pdf_view_link, '
            'created_at'
            ') VALUES (%s, %s, %s, %s, %s, %s )',
            (
                cv_id,
                f"https://docs.google.com/document/d/{template_response['new_doc_id']}/edit",
                f"https://drive.google.com/uc?export=download&id={docx_file.get('id')}&filename={output_filename}.docx",
                f"https://drive.google.com/uc?export=download&id={pdf_file.get('id')}&filename={output_filename}.pdf",
                f"https://drive.google.com/file/d/{pdf_file.get('id')}/view",
                datetime.now()
            )
        )

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "docx_edit_link": f"https://docs.google.com/document/d/{template_response['new_doc_id']}/edit",
            "docx_download_link": f"https://drive.google.com/uc?export=download&id={docx_file.get('id')}&"
                                  f"filename={output_filename}.docx",
            "pdf_download_link": f"https://drive.google.com/uc?export=download&id={pdf_file.get('id')}&"
                                 f"filename={output_filename}.pdf",
            "pdf_view_link": f"https://drive.google.com/file/d/{pdf_file.get('id')}/view"
        }

    except Exception as e:
        content = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        print(f"Error at makeDocFromTemplate: {content}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


def suggestionEngine(data) -> None:
    conn = psycopg2.connect(conn_str)
    cursor = conn.cursor()

    query = '''
        SELECT
            co_occurance_frequency
        FROM
            startrek.keywords_frequencies
        WHERE
            id = 1
    '''
    cursor.execute(query)
    result = cursor.fetchone()
    unpacked_data = result[0]

    result_dict = {
        "career_objective_keywords": {},
        "work_expertise_keywords": {},
        "co_curricular_activities": {},
        "skills": {}
    }

    if unpacked_data:
        result_dict = {key: value for key, value in result[0].items()}

    def process_keywords(keyword_block: [str], field_type: str):
        for index, item in enumerate(keyword_block):
            if item.lower() not in result_dict[field_type]:
                result_dict[field_type][item.lower()] = {}

            for idx, val in enumerate(keyword_block):
                if val == item:
                    continue
                if val.lower() in result_dict[field_type][item.lower()]:
                    result_dict[field_type][item.lower()][val.lower()] += 1
                else:
                    result_dict[field_type][item.lower()][val.lower()] = 1

    if 'career_objective_keywords' in data:
        process_keywords(data['career_objective_keywords'], "career_objective_keywords")

    if 'work_expertise_keywords' in data:
        for keyword_list in data['work_expertise_keywords']:
            if len(keyword_list):
                process_keywords(keyword_list, "work_expertise_keywords")

    if 'co_curricular_activities' in data:
        process_keywords(data['co_curricular_activities'], "co_curricular_activities")

    if 'skills' in data:
        process_keywords(data['skills'], "skills")

    update_query = '''
        UPDATE
            startrek.keywords_frequencies
        SET
            co_occurance_frequency = %s
        WHERE
            id = 1
    '''

    cursor.execute(update_query, (json.dumps(result_dict),))
    conn.commit()

    cursor.close()
    conn.close()

    return


async def cvtemplate_2(service, docs_service, data, working_experiences, career_objective):
    try:
        requests = []
        doc_id_ai_resume = '1RkUuaowIkKRqtU0UaOXd_QNzFJHPhZcQc4I8oZdq770'
        output_filename = f"{data.full_name}_{time.time()}"
        copied_file = service.files().copy(
            fileId=doc_id_ai_resume, body={
                'name': output_filename
            }).execute()
        #
        service.permissions().create(
            fileId=copied_file.get('id'),
            body={
                'type': 'anyone',
                'role': 'writer',
            },
        ).execute()
        #
        new_doc_id = copied_file.get('id')

        service.files().update(
            fileId=new_doc_id,
            removeParents='root',
            addParents='1HnYU7heYpdt0K9-eFKozq2q6FkU_M1kK'
        ).execute()

        print("Document URL", f"https://docs.google.com/document/d/{new_doc_id}/edit")

        # document = docs_service.documents().get(documentId=new_doc_id).execute()
        # content = document.get('body').get('content')
        # print("content", content)

        skill_rplc = ''
        co_curricular_rplc = ''
        for index, skill in enumerate(data.skills):
            if index == len(data.skills) - 1:
                skill_rplc += skill
            else:
                skill_rplc += skill + '\n'

        for index, activity in enumerate(data.co_curricular_activities):
            if index == len(data.co_curricular_activities) - 1:
                co_curricular_rplc += activity
            else:
                co_curricular_rplc += activity + '\n'

        requests.extend([
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{career_objective}}',
                        'matchCase': True
                    },
                    'replaceText': career_objective
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{name}}',
                        'matchCase': True
                    },
                    'replaceText': data.full_name
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{address}}',
                        'matchCase': True
                    },
                    'replaceText': data.address
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{contact_no}}',
                        'matchCase': True
                    },
                    'replaceText': data.contact_no
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{email}}',
                        'matchCase': True
                    },
                    'replaceText': data.email
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{skills}}',
                        'matchCase': True
                    },
                    'replaceText': skill_rplc
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{co_curricular}}',
                        'matchCase': True
                    },
                    'replaceText': co_curricular_rplc
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{reference_name}}',
                        'matchCase': True
                    },
                    'replaceText': data.references[0].full_name
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{REFRENCE_COMPANY}}',
                        'matchCase': True
                    },
                    'replaceText': data.references[0].company
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{reference_contact_no}}',
                        'matchCase': True
                    },
                    'replaceText': data.references[0].contact_no
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{reference_email}}',
                        'matchCase': True
                    },
                    'replaceText': data.references[0].email
                }
            }
        ]
        )

        starting_index = 188
        working_experience_replace = []
        if len(working_experiences):
            inserting_index = starting_index
            for i in range(0, len(working_experiences)):
                if i != 0:
                    replace_text = f"job_title_{i + 1} - JOB_COMPANY_NAME_{i + 1} (job_start_date_{i + 1} - " \
                                   f"job_end_date_{i + 1})\nwork_expertise_{i + 1}\n"
                    starting_index = starting_index + len(replace_text)
                    temp_requests = [{
                        "insertText": {
                            "location": {
                                "index": inserting_index
                            },
                            "text": replace_text
                        },
                    }, {
                        "updateTextStyle": {
                            "range": {
                                "startIndex": inserting_index,
                                "endIndex": inserting_index + len(replace_text),
                            },
                            "textStyle": {
                                "foregroundColor": {
                                    "color": {
                                        "rgbColor": {
                                            "red": 0.07058824,
                                            "green": 0.078431375,
                                            "blue": 0.13333334
                                        }
                                    }
                                },
                                "fontSize": {
                                    "magnitude": 12,
                                    "unit": "PT"
                                },
                                "weightedFontFamily": {
                                    "fontFamily": "Alice",
                                    "weight": 400
                                }
                            },
                            "fields": "foregroundColor,fontSize,weightedFontFamily"
                        }
                    },
                        {
                            "updateTextStyle": {
                                "range": {
                                    "startIndex": inserting_index + len(replace_text) - len("{{work_expertise_0}}"),
                                    "endIndex": inserting_index + len(replace_text),
                                },
                                "textStyle": {
                                    "foregroundColor": {
                                        "color": {
                                            "rgbColor": {
                                                "red": 0.46666667,
                                                "green": 0.4627451,
                                                "blue": 0.46666667
                                            }
                                        }
                                    },
                                    "fontSize": {
                                        "magnitude": 12,
                                        "unit": "PT"
                                    },
                                    "weightedFontFamily": {
                                        "fontFamily": "Alice",
                                        "weight": 400
                                    }
                                },
                                "fields": "foregroundColor,fontSize,weightedFontFamily"
                                # Specify which fields to update
                            }
                        }
                    ]
                    docs_service.documents().batchUpdate(
                        documentId=new_doc_id,
                        body={'requests': temp_requests}
                    ).execute()
                end_date = working_experiences[i]["end_date"] if working_experiences[i][
                                                                     "end_date"] is not None else 'Present'
                working_experience_replace.extend([
                    {
                        'replaceAllText': {
                            'containsText': {
                                'text': f"job_title_{i + 1}",
                                'matchCase': True
                            },
                            'replaceText': working_experiences[i]["designation"]
                        }
                    },
                    {
                        'replaceAllText': {
                            'containsText': {
                                'text': f"JOB_COMPANY_NAME_{i + 1}",
                                'matchCase': True
                            },
                            'replaceText': working_experiences[i]["org_name"]
                        }
                    },
                    {
                        'replaceAllText': {
                            'containsText': {
                                'text': f"job_start_date_{i + 1}",
                                'matchCase': True
                            },
                            'replaceText': working_experiences[i]["start_date"]
                        }
                    },
                    {
                        'replaceAllText': {
                            'containsText': {
                                'text': f"job_end_date_{i + 1}",
                                'matchCase': True
                            },
                            'replaceText': end_date
                        }
                    },
                    {
                        'replaceAllText': {
                            'containsText': {
                                'text': f"work_expertise_{i + 1}",
                                'matchCase': True
                            },
                            'replaceText': working_experiences[i]["work_expertise_generative_response"]
                        }
                    }
                ])

            requests.extend(working_experience_replace)
        else:
            temp_requests = [
                {
                    "deleteContentRange": {
                        "range": {
                            "startIndex": 89,  # Start index of the row to delete
                            "endIndex": 188  # End index of the row to delete
                        }
                    }
                }
            ]
            docs_service.documents().batchUpdate(
                documentId=new_doc_id,
                body={'requests': temp_requests}
            ).execute()

        education_replace = []
        education_starting_index = starting_index + 95 if len(working_experiences) else 183
        for i in range(0, len(data.educations)):
            if i != 0:
                replace_text = f"education_name_{i + 1}\neducation_inst_{i + 1} (education_start_year_{i + 1} - " \
                               f"education_end_year_{i + 1})\n"
                inserting_index = education_starting_index
                education_starting_index = education_starting_index + len(replace_text)
                temp_requests = [{
                    "insertText": {
                        "location": {
                            "index": inserting_index
                        },
                        "text": replace_text
                    },
                }, {
                    "updateTextStyle": {
                        "range": {
                            "startIndex": inserting_index,
                            "endIndex": inserting_index + len("education_name_0"),
                        },
                        "textStyle": {
                            "foregroundColor": {
                                "color": {
                                    "rgbColor": {
                                        "red": 0.07058824,
                                        "green": 0.078431375,
                                        "blue": 0.13333334
                                    }
                                }
                            },
                            "fontSize": {
                                "magnitude": 12,
                                "unit": "PT"
                            },
                            "weightedFontFamily": {
                                "fontFamily": "Alice",
                                "weight": 400
                            }
                        },
                        "fields": "foregroundColor,fontSize,weightedFontFamily"
                    }
                }, {
                    "updateTextStyle": {
                        "range": {
                            "startIndex": inserting_index + len("education_name_0"),
                            "endIndex": inserting_index + len(replace_text),
                        },
                        "textStyle": {
                            "foregroundColor": {
                                "color": {
                                    "rgbColor": {
                                        "red": 0.46666667,
                                        "green": 0.4627451,
                                        "blue": 0.46666667
                                    }
                                }
                            },
                            "fontSize": {
                                "magnitude": 12,
                                "unit": "PT"
                            },
                            "weightedFontFamily": {
                                "fontFamily": "Alice",
                                "weight": 400
                            }
                        },
                        "fields": "foregroundColor,fontSize,weightedFontFamily"
                    }
                }]
                docs_service.documents().batchUpdate(
                    documentId=new_doc_id,
                    body={'requests': temp_requests}
                ).execute()
            education_replace.extend([
                {
                    'replaceAllText': {
                        'containsText': {
                            'text': f"education_name_{i + 1}",
                            'matchCase': True
                        },
                        'replaceText': data.educations[i].education_level
                    }
                },
                {
                    'replaceAllText': {
                        'containsText': {
                            'text': f"education_inst_{i + 1}",
                            'matchCase': True
                        },
                        'replaceText': data.educations[i].institute_name
                    }
                },
                {
                    'replaceAllText': {
                        'containsText': {
                            'text': f"education_start_year_{i + 1}",
                            'matchCase': True
                        },
                        'replaceText': data.educations[i].starting_year
                    }
                },
                {
                    'replaceAllText': {
                        'containsText': {
                            'text': f"education_end_year_{i + 1}",
                            'matchCase': True
                        },
                        'replaceText': data.educations[i].passing_year
                    }
                }
            ])
        requests.extend(education_replace)

        if data.image:
            cdn_url = "https://ppol-web-uploads.s3.ap-southeast-1.amazonaws.com/"
            temp_requests = [{
                "insertInlineImage": {
                    "uri": cdn_url + data.image,
                    "location": {
                        "index": 5
                    },
                    "objectSize": {
                        "height": {
                            "magnitude": 500,
                            "unit": "PT"
                        },
                        "width": {
                            "magnitude": 150,
                            "unit": "PT"
                        }
                    }
                }
            }]
            docs_service.documents().batchUpdate(
                documentId=new_doc_id,
                body={'requests': temp_requests}
            ).execute()

        return {
            "requests": requests,
            "new_doc_id": new_doc_id
        }

    except Exception as e:
        content = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        print(f"Error at CV Template 2: {content}")
        raise HTTPException(status_code=500, detail="Connection Failed!")


async def cvtemplate_1(service, docs_service, data, working_experiences, career_objective):
    try:
        requests = []
        doc_id_ai_resume = '1AKnaAZh-BKjTww-dX6H-9eya4A3WGYyP3x4Sae8dga0'
        output_filename = f"{data.full_name}_{time.time()}"
        copied_file = service.files().copy(
            fileId=doc_id_ai_resume, body={
                'name': output_filename
            }).execute()

        service.permissions().create(
            fileId=copied_file.get('id'),
            body={
                'type': 'anyone',
                'role': 'writer',
            },
        ).execute()
        #
        new_doc_id = copied_file.get('id')

        service.files().update(
            fileId=new_doc_id,
            removeParents='root',
            addParents='1HnYU7heYpdt0K9-eFKozq2q6FkU_M1kK'
        ).execute()

        print("Document URL", f"https://docs.google.com/document/d/{new_doc_id}/edit")

        # document = docs_service.documents().get(documentId=new_doc_id).execute()
        # content = document.get('body').get('content')
        # print("content", content)

        skill_rplc = ''
        co_curricular_rplc = ''
        for index, skill in enumerate(data.skills):
            if index == len(data.skills) - 1:
                skill_rplc += skill
            else:
                skill_rplc += skill + '\n'

        for index, activity in enumerate(data.co_curricular_activities):
            if index == len(data.co_curricular_activities) - 1:
                co_curricular_rplc += activity
            else:
                co_curricular_rplc += activity + '\n'

        requests.extend([
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{career_objective}}',
                        'matchCase': True
                    },
                    'replaceText': career_objective
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{name}}',
                        'matchCase': True
                    },
                    'replaceText': data.full_name
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{address}}',
                        'matchCase': True
                    },
                    'replaceText': data.address
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{contact_no}}',
                        'matchCase': True
                    },
                    'replaceText': data.contact_no
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{email}}',
                        'matchCase': True
                    },
                    'replaceText': data.email
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{skills}}',
                        'matchCase': True
                    },
                    'replaceText': skill_rplc
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{co_curricular}}',
                        'matchCase': True
                    },
                    'replaceText': co_curricular_rplc
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{reference_name}}',
                        'matchCase': True
                    },
                    'replaceText': data.references[0].full_name
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{REFRENCE_COMPANY}}',
                        'matchCase': True
                    },
                    'replaceText': data.references[0].company
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{reference_contact_no}}',
                        'matchCase': True
                    },
                    'replaceText': data.references[0].contact_no
                }
            },
            {
                'replaceAllText': {
                    'containsText': {
                        'text': '{{reference_email}}',
                        'matchCase': True
                    },
                    'replaceText': data.references[0].email
                }
            }
        ]
        )

        if data.image:
            cdn_url = "https://ppol-web-uploads.s3.ap-southeast-1.amazonaws.com/"
            temp_requests = [{
                "insertInlineImage": {
                    "uri": cdn_url + data.image,
                    "location": {
                        "index": 330
                    },
                    "objectSize": {
                        "height": {
                            "magnitude": 2.54 * 72,
                            "unit": "PT"
                        },
                        "width": {
                            "magnitude": 1.68 * 72,
                            "unit": "PT"
                        }
                    }
                }
            }]
            docs_service.documents().batchUpdate(
                documentId=new_doc_id,
                body={'requests': temp_requests}
            ).execute()

        starting_index = 136
        working_experience_replace = []
        if len(working_experiences):
            inserting_index = starting_index
            for i in range(0, len(working_experiences)):
                if i != 0:
                    replace_text = f"job_title_{i + 1} - JOB_COMPANY_NAME_{i + 1} (job_start_date_{i + 1} - " \
                                   f"job_end_date_{i + 1})\nwork_expertise_{i + 1}\n"
                    starting_index = starting_index + len(replace_text)
                    temp_requests = [{
                        "insertText": {
                            "location": {
                                "index": inserting_index
                            },
                            "text": replace_text
                        },
                    }, {
                        "updateTextStyle": {
                            "range": {
                                "startIndex": inserting_index,
                                "endIndex": starting_index - len("work_expertise_0"),
                            },
                            "textStyle": {
                                "foregroundColor": {
                                    "color": {
                                        "rgbColor": {
                                            "red": 0.11372549,
                                            "green": 0.11372549,
                                            "blue": 0.11372549
                                        }
                                    }
                                },
                                "fontSize": {
                                    "magnitude": 12,
                                    "unit": "PT"
                                },
                                "weightedFontFamily": {
                                    "fontFamily": "Open Sans",
                                    "weight": 600
                                }
                            },
                            "fields": "foregroundColor,fontSize,weightedFontFamily"
                        }
                    },
                        {
                            "updateTextStyle": {
                                "range": {
                                    "startIndex": inserting_index + len(replace_text) - len("\nwork_expertise_0\n"),
                                    "endIndex": inserting_index + len(replace_text),
                                },
                                "textStyle": {
                                    "foregroundColor": {
                                        "color": {
                                            "rgbColor": {
                                                "red": 0.11372549,
                                                "green": 0.11372549,
                                                "blue": 0.11372549
                                            }
                                        }
                                    },
                                    "fontSize": {
                                        "magnitude": 12,
                                        "unit": "PT"
                                    },
                                    "weightedFontFamily": {
                                        "fontFamily": "Open Sans",
                                        "weight": 300
                                    }
                                },
                                "fields": "foregroundColor,fontSize,weightedFontFamily"
                                # Specify which fields to update
                            }
                        },
                        {
                            "updateParagraphStyle": {
                                "range": {
                                    "startIndex": inserting_index + len(replace_text) - len("\nwork_expertise_0\n"),
                                    "endIndex": inserting_index + len(replace_text),
                                },
                                "paragraphStyle": {
                                    "namedStyleType": "NORMAL_TEXT",
                                    "alignment": "JUSTIFIED",
                                    "lineSpacing": 100,
                                    "direction": "LEFT_TO_RIGHT",
                                    "spacingMode": "NEVER_COLLAPSE",
                                    "spaceBelow": {
                                        "magnitude": 10,
                                        "unit": "PT"
                                    },
                                    "avoidWidowAndOrphan": False
                                },
                                "fields": "alignment,indentStart,spaceAbove,spaceBelow"
                                # Specify which fields to update
                            }
                        }
                    ]
                    docs_service.documents().batchUpdate(
                        documentId=new_doc_id,
                        body={'requests': temp_requests}
                    ).execute()
                end_date = working_experiences[i]["end_date"] if working_experiences[i][
                                                                     "end_date"] is not None else 'Present'
                working_experience_replace.extend([
                    {
                        'replaceAllText': {
                            'containsText': {
                                'text': f"job_title_{i + 1}",
                                'matchCase': True
                            },
                            'replaceText': working_experiences[i]["designation"]
                        }
                    },
                    {
                        'replaceAllText': {
                            'containsText': {
                                'text': f"JOB_COMPANY_NAME_{i + 1}",
                                'matchCase': True
                            },
                            'replaceText': working_experiences[i]["org_name"]
                        }
                    },
                    {
                        'replaceAllText': {
                            'containsText': {
                                'text': f"job_start_date_{i + 1}",
                                'matchCase': True
                            },
                            'replaceText': working_experiences[i]["start_date"]
                        }
                    },
                    {
                        'replaceAllText': {
                            'containsText': {
                                'text': f"job_end_date_{i + 1}",
                                'matchCase': True
                            },
                            'replaceText': end_date
                        }
                    },
                    {
                        'replaceAllText': {
                            'containsText': {
                                'text': f"work_expertise_{i + 1}",
                                'matchCase': True
                            },
                            'replaceText': working_experiences[i]["work_expertise_generative_response"]
                        }
                    }
                ])

            requests.extend(working_experience_replace)
        else:
            temp_requests = [
                {
                    "deleteContentRange": {
                        "range": {
                            "startIndex": 38,  # Start index of the row to delete
                            "endIndex": 136  # End index of the row to delete
                        }
                    }
                }
            ]
            docs_service.documents().batchUpdate(
                documentId=new_doc_id,
                body={'requests': temp_requests}
            ).execute()

        education_replace = []
        education_starting_index = starting_index + 94 if len(working_experiences) else 134
        for i in range(0, len(data.educations)):
            if i != 0:
                replace_text = f"education_inst_{i + 1} (education_start_year_{i + 1} - " \
                               f"education_end_year_{i + 1})\neducation_name_{i + 1}\n"
                inserting_index = education_starting_index
                education_starting_index = education_starting_index + len(replace_text)
                temp_requests = [{
                    "insertText": {
                        "location": {
                            "index": inserting_index
                        },
                        "text": replace_text
                    },
                }, {
                    "updateTextStyle": {
                        "range": {
                            "startIndex": inserting_index,
                            "endIndex": inserting_index + len(replace_text) - len("education_name_0"),
                        },
                        "textStyle": {
                            "foregroundColor": {
                                "color": {
                                    "rgbColor": {
                                        "red": 0.11372549,
                                        "green": 0.11372549,
                                        "blue": 0.11372549
                                    }
                                }
                            },
                            "fontSize": {
                                "magnitude": 12,
                                "unit": "PT"
                            },
                            "weightedFontFamily": {
                                "fontFamily": "Open Sans",
                                "weight": 600
                            }
                        },
                        "fields": "foregroundColor,fontSize,weightedFontFamily"
                    }
                }, {
                    "updateTextStyle": {
                        "range": {
                            "startIndex": inserting_index + len(replace_text) - len("\neducation_name_0"),
                            "endIndex": education_starting_index,
                        },
                        "textStyle": {
                            "foregroundColor": {
                                "color": {
                                    "rgbColor": {
                                        "red": 0.11372549,
                                        "green": 0.11372549,
                                        "blue": 0.11372549
                                    }
                                }
                            },
                            "fontSize": {
                                "magnitude": 12,
                                "unit": "PT"
                            },
                            "weightedFontFamily": {
                                "fontFamily": "Open Sans",
                                "weight": 300
                            }
                        },
                        "fields": "foregroundColor,fontSize,weightedFontFamily"
                    }
                }]
                docs_service.documents().batchUpdate(
                    documentId=new_doc_id,
                    body={'requests': temp_requests}
                ).execute()
            education_replace.extend([
                {
                    'replaceAllText': {
                        'containsText': {
                            'text': f"education_name_{i + 1}",
                            'matchCase': True
                        },
                        'replaceText': data.educations[i].education_level
                    }
                },
                {
                    'replaceAllText': {
                        'containsText': {
                            'text': f"education_inst_{i + 1}",
                            'matchCase': True
                        },
                        'replaceText': data.educations[i].institute_name
                    }
                },
                {
                    'replaceAllText': {
                        'containsText': {
                            'text': f"education_start_year_{i + 1}",
                            'matchCase': True
                        },
                        'replaceText': data.educations[i].starting_year
                    }
                },
                {
                    'replaceAllText': {
                        'containsText': {
                            'text': f"education_end_year_{i + 1}",
                            'matchCase': True
                        },
                        'replaceText': data.educations[i].passing_year
                    }
                }
            ])
        requests.extend(education_replace)

        return {
            "requests": requests,
            "new_doc_id": new_doc_id
        }

    except Exception as e:
        content = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        print(f"Error at CV Template 2: {content}")
        raise HTTPException(status_code=500, detail="Connection Failed!")
