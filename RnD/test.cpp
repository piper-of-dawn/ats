 
 // Sample output 
 // unordered_map<string, string> resolvedMapping = 
 //     "CURVE_1", "CURVE_3"},
 //     "CURVE_2", "CURVE_3"},
 //     "CURVE_4", "CURVE_3"},
 //     "CURVE_5", "CURVE_3"},
 //     "CURVE_10", "CURVE_20"},
 //     "CURVE_30", "CURVE_20"}
 // };
string test(unordered_map<string, string> hashmap, string start_node) {
    // unordered_set<string> visited;
    string current_node = start_node;
    int i = 0;
    while (hashmap.count(current_node)) {
        // visited.add(current_node);
        current_node = hashmap[current_node]
    }
    // code to be executed
    return current_node;
  }


int main () {
    unordered_map<string, string> curveMapping = {
        {"CURVE_1", "CURVE_2"},
        {"CURVE_2", "CURVE_3"},
        {"CURVE_4", "CURVE_3"},
        {"CURVE_5", "CURVE_1"},
        {"CURVE_10", "CURVE_20"},
        {"CURVE_30", "CURVE_20"}
        };
    
    unordered_map<string, string> final; 
    for (auto i = curveMapping.begin(); i != curveMapping.end(); i++){
        string key = i.first();
        // string value = i.second();
        final[key] = test(curveMapping, key);
    }
    for (auto i = final.begin(); i != final.end(); i++){
        string key = i.first();
        string value = i.second();
        cout << key << ": " << value << '\n';
    }
return 0;
}

