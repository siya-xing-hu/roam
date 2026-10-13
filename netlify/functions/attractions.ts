import { handleAttractions } from "../../server/attractionsHandler";

export default async function handler(request: Request): Promise<Response> {
  return handleAttractions(request);
}

export const config = {
  path: "/api/attractions",
};
